import json
import math

import pytest

from pipeline_analytics.core.jurisdiction import Service
from pipeline_analytics.economics import Concession, OptionInput, evaluate_option, ramp_profile
from pipeline_analytics.hydraulics import GasStationInput, Profile
from pipeline_analytics.optimiser import DesignSpace, ScenarioTree, optimise_design, optimise_route
from pipeline_analytics.routing.sample import END, START, sample_aoi

CONC = Concession(operating_years=15)
BASE = OptionInput(
    service=Service.GAS,
    nps=20,
    design_pressure_pa_g=92e5,
    profile=Profile.flat(120e3, points=25),
    throughput=ramp_profile(6, 9, 5, 15),
    gas_stations=GasStationInput(supply_pressure_pa_g=50e5, delivery_min_pa_g=30e5),
    concession=CONC,
)
SPACE = DesignSpace(sizes=("16", "20", "24", "30"), grades=("X65", "X70"),
                    design_pressures_pa_g=(70e5, 92e5))  # fmt: skip


@pytest.fixture(scope="module")
def search():  # type: ignore[no-untyped-def]
    return optimise_design(BASE, SPACE)


def test_design_search_is_exhaustive_and_picks_minimum(search) -> None:  # type: ignore[no-untyped-def]
    assert len(search.evaluated) == 4 * 2 * 2
    feasible = [r for _, r in search.evaluated if r.feasible]
    assert search.best.lcc_pv == min(r.lcc_pv for r in feasible)
    # the winner re-evaluates to the same answer (deterministic)
    again = evaluate_option(search.best_input)
    assert again.lcc_pv == pytest.approx(search.best.lcc_pv)
    assert search.best_node.lcc_pv == pytest.approx(search.best.lcc_pv)


def test_infeasible_designs_recorded_with_reason(search) -> None:  # type: ignore[no-untyped-def]
    bad = [n for n in search.tree.results() if not n.feasible]
    assert bad, "16 inch should fail the velocity limit at this flow"
    assert all(n.issue for n in bad)
    assert all(math.isinf(n.lcc_pv) for n in bad)


def test_scenario_tree_json_and_table(search) -> None:  # type: ignore[no-untyped-def]
    rows = search.tree.table()
    assert len(rows) == 16
    data = json.loads(search.tree.to_json())
    assert len(data) == 16 and data[0]["changes"]["nps"] == "16"


def test_no_feasible_design_raises() -> None:
    tiny = DesignSpace(sizes=("8",), design_pressures_pa_g=(70e5,))
    with pytest.raises(ValueError, match="no feasible design"):
        optimise_design(BASE, tiny)


def test_tree_lineage_and_best_under_parent() -> None:
    t = ScenarioTree()
    root = t.add_group("study")
    a = t.add_group("case A", parent=root)
    s = optimise_design(BASE, DesignSpace(sizes=("24",), design_pressures_pa_g=(70e5,)),
                        tree=t, parent=a)  # fmt: skip
    assert t.lineage(s.best_node.id) == [s.best_node.id, a, root]
    assert t.best(parent=a).id == s.best_node.id
    with pytest.raises(ValueError):
        ScenarioTree().best()


def test_route_loop_converges_on_sample_area() -> None:
    base = OptionInput.model_validate(
        {**BASE.model_dump(), "profile": Profile.flat(38e3).model_dump()}
    )
    space = DesignSpace(sizes=("20", "24", "30"), design_pressures_pa_g=(70e5,))
    ro = optimise_route(sample_aoi(), START, END, base, space, k=2, max_iterations=3)
    assert ro.converged
    # The router's surface objective approximates LCC but is not identical to it, so a
    # re-route can come out marginally worse; the optimiser keeps the best found so far.
    assert ro.best_lcc_with_geohazards == pytest.approx(min(ro.history))
    assert abs(ro.history[-1] - ro.history[-2]) / ro.history[-2] < 0.005
    assert ro.best_route.protected_m == 0
    groups = [n for n in ro.tree.nodes if n.issue == "group"]
    assert any(n.label == "iteration 1" for n in groups)
    assert len(ro.iterations) >= 2  # re-routed at least once with the improved design


def test_parallel_matches_serial(search) -> None:  # type: ignore[no-untyped-def]
    par = optimise_design(BASE, SPACE, workers=2)
    assert [r.lcc_pv for _, r in par.evaluated] == [r.lcc_pv for _, r in search.evaluated]
    assert par.best.nps == search.best.nps
