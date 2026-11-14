"""Phase 5 optimiser: minimum lifecycle cost (PV of CAPEX + OPEX) over the concession.

* :func:`optimise_design`: for a fixed alignment, enumerate diameter x grade x
  design pressure (MAOP). Each candidate is fully evaluated (walls per
  location class with optional ALA checks, yearly hydraulics with phased
  stations and offtakes, CAPEX, OPEX, cash flow).
* :func:`optimise_route`: the outer loop on an AOI (plan 6.3):

  1. build the cost surface for the current best diameter and pressure, with
     the OPEX-equivalent length penalty of the current best option;
  2. generate distinct corridors and run :func:`optimise_design` on each;
  3. check every geohazard crossing (mapped faults, landslide / lateral-spread /
     subsidence zones in ``AOI.geohazards``) with the pipe-soil FE model for
     the corridor's best design; add the present value of any heavier-wall
     mitigation to its lifecycle cost, and rank corridors with a crossing
     that no standard wall can make safe last. Where a fault crossing fails,
     a realigned variant of that corridor (straight crossing leg at the best
     angle from the strain sweep) is added and evaluated as well, so heavier
     wall in place and realignment compete on lifecycle cost;
  4. take the best (corridor, design); stop if lifecycle cost improved by less
     than ``tol`` or the design repeats, otherwise re-route with it.

Every evaluation is recorded in a :class:`ScenarioTree`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field

from pydantic import Field

from pipeline_analytics.cost.rates import _Model
from pipeline_analytics.economics.option import OptionInput, OptionResult, evaluate_option
from pipeline_analytics.mechanical.pressure import LocationClass
from pipeline_analytics.optimiser.scenarios import ScenarioNode, ScenarioTree
from pipeline_analytics.routing.aoi import AOI
from pipeline_analytics.routing.cost_surface import CostSurfaceParams, build_cost_surface
from pipeline_analytics.routing.extract import RouteExtraction, extract_route, smooth_path_polyline
from pipeline_analytics.routing.geohazards import (
    BurialBasis,
    GeohazardAssessment,
    HazardCrossing,
    assess_geohazards,
    realign_fault_crossing,
)
from pipeline_analytics.routing.pathfinding import alternative_corridors
from pipeline_analytics.routing.penalties import opex_penalties

XY = tuple[float, float]


class DesignSpace(_Model):
    sizes: tuple[str | float, ...]
    grades: tuple[str, ...] = ("X70",)
    design_pressures_pa_g: tuple[float, ...] = Field(min_length=1)


def _variant(base: OptionInput, **update: object) -> OptionInput:
    """Validated copy (ADR 0006: no unvalidated model_copy for derived inputs)."""
    return OptionInput.model_validate({**base.model_dump(), **update})


@dataclass
class DesignSearch:
    best_input: OptionInput
    best: OptionResult
    evaluated: list[tuple[OptionInput, OptionResult]]
    tree: ScenarioTree
    best_node: ScenarioNode


def optimise_design(
    base: OptionInput,
    space: DesignSpace,
    *,
    tree: ScenarioTree | None = None,
    parent: int | None = None,
    iteration: int = 0,
    label: str = "",
    workers: int = 1,
) -> DesignSearch:
    """Evaluate every design in ``space``; ``workers`` > 1 evaluates in parallel processes."""
    tree = tree or ScenarioTree()
    inputs = [
        _variant(base, nps=n, grade=g, design_pressure_pa_g=p)
        for p in space.design_pressures_pa_g
        for g in space.grades
        for n in space.sizes
    ]
    if workers > 1:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(evaluate_option, inputs))
    else:
        results = [evaluate_option(i) for i in inputs]
    evaluated: list[tuple[OptionInput, OptionResult]] = []
    best: tuple[OptionInput, OptionResult, ScenarioNode] | None = None
    for inp, res in zip(inputs, results, strict=True):
        evaluated.append((inp, res))
        node = tree.add_result(
            inp, res, label=label or "design", parent=parent, iteration=iteration,
            nps=str(inp.nps), grade=inp.grade, maop_barg=round(inp.design_pressure_pa_g / 1e5, 2),
        )  # fmt: skip
        if res.feasible and (best is None or res.lcc_pv < best[1].lcc_pv):
            best = (inp, res, node)
    if best is None:
        raise ValueError("no feasible design in the design space")
    return DesignSearch(best[0], best[1], evaluated, tree, best[2])


@dataclass
class RouteIteration:
    iteration: int
    corridors: list[RouteExtraction]
    best_corridor: int
    best: DesignSearch
    lcc_pv: float
    """Best corridor's lifecycle cost including geohazard mitigation (PV)."""
    searches: list[DesignSearch] = field(default_factory=list)
    geohazards: list[GeohazardAssessment | None] = field(default_factory=list)
    """Per corridor: strain checks at fault / ground-movement crossings."""
    geohazard_pv: list[float] = field(default_factory=list)
    """Per corridor: PV of heavier-wall mitigation added to lifecycle cost."""
    labels: list[str] = field(default_factory=list)
    """Corridor names: C1, C2, ... and realigned variants (e.g. "C1-realigned 30 deg")."""


def design_geohazards(
    crossings: Sequence[HazardCrossing],
    class_at: Callable[[float], LocationClass],
    inp: OptionInput,
    res: OptionResult,
    basis: BurialBasis | None = None,
) -> GeohazardAssessment | None:
    """Strain checks at geohazard crossings for one evaluated design, using the wall
    of the location class at each crossing."""
    if not crossings or not res.feasible:
        return None
    wall_of = {w.location_class: w.wt_m for w in res.walls}
    default = min(wall_of.values())
    walls = [wall_of.get(class_at(c.chainage_m), default) for c in crossings]
    process = res.walls[0].process if res.walls else "SAWL"
    return assess_geohazards(
        crossings, nps=inp.nps, grade=inp.grade, wall_m=walls,
        pressure_pa=inp.design_pressure_pa_g, rates=inp.rates,
        basis=basis or _basis_of(inp), process=process,
    )  # fmt: skip


def corridor_geohazards(
    ex: RouteExtraction,
    inp: OptionInput,
    res: OptionResult,
    basis: BurialBasis | None = None,
) -> GeohazardAssessment | None:
    """Strain checks at this corridor's geohazard crossings for one evaluated design."""
    return design_geohazards(ex.geohazard_crossings, ex.location_class_at, inp, res, basis)


def _evaluate_corridor(
    ex: RouteExtraction,
    label: str,
    inp: OptionInput,
    space: DesignSpace,
    tree: ScenarioTree,
    parent: int,
    it: int,
    basis: BurialBasis | None,
) -> tuple[DesignSearch, GeohazardAssessment | None]:
    node = tree.add_group(f"corridor {label}", parent=parent, iteration=it,
                          length_km=round(ex.length_m / 1000, 2))  # fmt: skip
    sr = optimise_design(ex.option_input(inp), space, tree=tree, parent=node, iteration=it,
                         label=f"it{it} {label}")  # fmt: skip
    return sr, corridor_geohazards(ex, sr.best_input, sr.best, basis)


def _basis_of(inp: OptionInput) -> BurialBasis:
    b = inp.burial
    if b is None:
        return BurialBasis()
    return BurialBasis(soil=b.soil, cover_m=b.cover_m, coating=b.coating)


def mitigation_pv(a: GeohazardAssessment | None, res: OptionResult) -> float:
    if a is None or res.capex is None or not res.capex.total:
        return 0.0
    return a.mitigation_cost * res.pv_capex / res.capex.total


@dataclass
class RouteOptimisation:
    best_input: OptionInput
    best: OptionResult
    best_route: RouteExtraction
    iterations: list[RouteIteration]
    tree: ScenarioTree
    converged: bool
    history: list[float] = field(default_factory=list)
    best_geohazards: GeohazardAssessment | None = None
    best_lcc_with_geohazards: float = float("nan")


def optimise_route(
    aoi: AOI,
    start: XY,
    end: XY,
    base: OptionInput,
    space: DesignSpace,
    *,
    k: int = 3,
    max_iterations: int = 4,
    tol: float = 0.005,
    waypoints: Sequence[XY] = (),
    surface_overrides: dict[str, object] | None = None,
    geohazard_basis: BurialBasis | None = None,
    realign_faults: bool = True,
) -> RouteOptimisation:
    tree = ScenarioTree()
    current = base
    iterations: list[RouteIteration] = []
    history: list[float] = []
    best_overall: tuple[OptionInput, OptionResult, RouteExtraction] | None = None
    best_lcc = float("inf")
    best_geo: GeohazardAssessment | None = None
    last_key: tuple[object, ...] | None = None
    converged = False
    for it in range(1, max_iterations + 1):
        pen = opex_penalties(current)
        params = CostSurfaceParams.model_validate(
            {
                "service": current.service,
                "nps": current.nps,
                "grade": current.grade,
                "psl": current.psl,
                "design_pressure_pa_g": current.design_pressure_pa_g,
                "corrosion_allowance_m": current.corrosion_allowance_m,
                "rates": current.rates.model_dump(),
                "length_per_m": pen.length_per_m,
                **(surface_overrides or {}),
            }
        )
        surface = build_cost_surface(aoi, params)
        alts = alternative_corridors(surface, start, end, k=k, waypoints=waypoints)
        it_node = tree.add_group(
            f"iteration {it}", iteration=it, routed_for=f'{current.nps}" @ '
            f"{current.design_pressure_pa_g / 1e5:.0f} barg",
        )  # fmt: skip
        exs = [extract_route(surface, smooth_path_polyline(surface, a.cells)) for a in alts]
        labels = [f"C{i + 1}" for i in range(len(exs))]
        searches: list[DesignSearch] = []
        geo: list[GeohazardAssessment | None] = []

        def evaluate(ex: RouteExtraction, label: str, inp: OptionInput = current,
                     parent: int = it_node, it: int = it) -> None:  # fmt: skip
            sr, a = _evaluate_corridor(ex, label, inp, space, tree, parent, it, geohazard_basis)
            searches.append(sr)  # noqa: B023 - lists are rebuilt each iteration, used at once
            geo.append(a)  # noqa: B023

        for ex, label in zip(list(exs), list(labels), strict=True):
            evaluate(ex, label)
        if realign_faults and aoi.geohazards and not waypoints:
            for ci in range(len(exs)):
                a = geo[ci]
                bad = next((c for c in (a.checks if a else ())
                            if c.crossing.kind == "fault" and not c.passed
                            and c.best_crossing_angle_deg is not None), None)  # fmt: skip
                if bad is None:
                    continue
                fault = next(f for f in aoi.geohazards.faults if f.name == bad.crossing.hazard)
                assert bad.best_crossing_angle_deg is not None
                line = realign_fault_crossing(surface, start, end, bad.crossing, fault,
                                              bad.best_crossing_angle_deg)  # fmt: skip
                exs.append(extract_route(surface, line))
                labels.append(f"{labels[ci]}-realigned {bad.best_crossing_angle_deg:g} deg")
                evaluate(exs[-1], labels[-1])
        geo_pv = [mitigation_pv(a, sr.best) for a, sr in zip(geo, searches, strict=True)]

        keys = [
            (a is not None and bool(a.unmitigated), sr.best.lcc_pv + pv)
            for a, sr, pv in zip(geo, searches, geo_pv, strict=True)
        ]
        bi = min(range(len(searches)), key=keys.__getitem__)
        s = searches[bi]
        lcc = s.best.lcc_pv + geo_pv[bi]
        iterations.append(RouteIteration(it, exs, bi, s, lcc, searches, geo, geo_pv, labels))
        history.append(lcc)
        if best_overall is None or lcc < best_lcc:
            best_overall = (s.best_input, s.best, exs[bi])
            best_lcc, best_geo = lcc, geo[bi]
        key = (s.best.nps, s.best.grade, s.best_input.design_pressure_pa_g,
               round(exs[bi].length_m, -2))  # fmt: skip
        if len(history) > 1 and (key == last_key or (history[-2] - lcc) / history[-2] < tol):
            converged = True
            break
        last_key = key
        current = s.best_input
    assert best_overall is not None
    return RouteOptimisation(
        best_input=best_overall[0],
        best=best_overall[1],
        best_route=best_overall[2],
        iterations=iterations,
        tree=tree,
        converged=converged,
        history=history,
        best_geohazards=best_geo,
        best_lcc_with_geohazards=best_lcc,
    )
