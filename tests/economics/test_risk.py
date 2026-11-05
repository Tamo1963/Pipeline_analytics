import math
import random

import numpy as np
import pytest
from pydantic import ValidationError

from pipeline_analytics.core.jurisdiction import Service
from pipeline_analytics.cost.rates import (
    RATE_GROUPS,
    placeholder_rates,
    scale_capital,
    scale_rate_groups,
)
from pipeline_analytics.economics import Concession, OptionInput, evaluate_option, ramp_profile
from pipeline_analytics.economics.risk import (
    RiskInputs,
    ThroughputScenario,
    Triangular,
    check_linearity,
    monte_carlo,
)
from pipeline_analytics.hydraulics import GasStationInput, Profile

BASE = OptionInput(
    service=Service.GAS,
    nps="24",
    design_pressure_pa_g=92e5,
    profile=Profile.flat(150e3, points=31),
    throughput=ramp_profile(6, 9, 5, 15),
    gas_stations=GasStationInput(supply_pressure_pa_g=50e5, delivery_min_pa_g=30e5),
    concession=Concession(operating_years=15),
)


def design(nps: str) -> OptionInput:
    return OptionInput.model_validate({**BASE.model_dump(), "nps": nps})


def test_rate_groups_cover_and_scale() -> None:
    r = placeholder_rates()
    s = scale_rate_groups(r, {"line_pipe": 2.0, "energy": 0.5})
    assert s.line_pipe.base_rate_per_tonne == 2 * r.line_pipe.base_rate_per_tonne
    assert s.opex.fuel_gas_per_scm == 0.5 * r.opex.fuel_gas_per_scm
    assert s.opex.insurance_fraction == r.opex.insurance_fraction  # % items in no group
    assert s.indirects == r.indirects
    c = scale_capital(r, 3.0)
    assert c.stations.compressor_fixed == 3 * r.stations.compressor_fixed
    assert c.opex == r.opex
    with pytest.raises(KeyError):
        scale_rate_groups(r, {"nonsense": 1.0})


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_lcc_is_exactly_linear_in_rate_groups(seed: int) -> None:
    """The basis of the fast Monte Carlo: exact, not approximate."""
    rnd = random.Random(seed)
    f = {g: rnd.uniform(0.5, 1.8) for g in RATE_GROUPS}
    lin, direct = check_linearity(BASE, f)
    assert lin == pytest.approx(direct, rel=1e-12)


def test_triangular_validation_and_sampling() -> None:
    t = Triangular(low=0.8, mode=1.0, high=1.4)
    x = t.sample(np.random.default_rng(0), 200_000)
    assert x.min() >= 0.8 and x.max() <= 1.4
    assert x.mean() == pytest.approx(t.mean, rel=0.005)
    with pytest.raises(ValidationError):
        Triangular(low=1.2, mode=1.0, high=1.4)
    with pytest.raises(ValidationError):
        RiskInputs(scenarios=[ThroughputScenario(name="a", probability=0.5, factor=1)])
    with pytest.raises(ValidationError):
        RiskInputs(groups={"steel": Triangular(low=0.9, mode=1, high=1.1)})


@pytest.fixture(scope="module")
def mc():  # type: ignore[no-untyped-def]
    return monte_carlo([design("20"), design("24"), design("30")], RiskInputs(samples=20_000))


def test_monte_carlo_outputs(mc) -> None:  # type: ignore[no-untyped-def]
    assert mc.evaluations == 3 * 3 * (1 + 6)
    by = {d.label.split('"')[0]: d for d in mc.designs}
    # 20" fails the velocity limit in every scenario
    assert by["20"].prob_infeasible == 1.0 and math.isinf(by["20"].lcc_p50)
    # 24" fails only in the high-throughput scenario (25 % weight)
    assert by["24"].prob_infeasible == pytest.approx(0.25, abs=0.015)
    assert math.isinf(by["24"].lcc_p90)
    d30 = by["30"]
    assert d30.prob_infeasible == 0
    assert d30.lcc_p10 < d30.lcc_p50 < d30.lcc_p90
    assert sum(d.prob_cheapest for d in mc.designs) == pytest.approx(1.0)
    # deterministic value equals a plain evaluation at base throughput and rates
    assert d30.deterministic_lcc == pytest.approx(evaluate_option(design("30")).lcc_pv)


def test_same_seed_same_answer() -> None:
    a = monte_carlo([design("30")], RiskInputs(samples=500, seed=7))
    b = monte_carlo([design("30")], RiskInputs(samples=500, seed=7))
    assert a.designs[0].lcc_p50 == b.designs[0].lcc_p50


def test_degenerate_case_reproduces_deterministic() -> None:
    """Tight ranges and one scenario: every percentile equals the deterministic LCC."""
    tight = {g: Triangular(low=0.999999, mode=1.0, high=1.000001) for g in RATE_GROUPS}
    r = monte_carlo(
        [design("30")],
        RiskInputs(
            groups=tight,
            scenarios=[ThroughputScenario(name="b", probability=1, factor=1)],
            samples=200,
        ),
    )
    d = r.designs[0]
    assert d.lcc_p10 == pytest.approx(d.deterministic_lcc, rel=1e-5)
    assert d.lcc_p90 == pytest.approx(d.deterministic_lcc, rel=1e-5)
