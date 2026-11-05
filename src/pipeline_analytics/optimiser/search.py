"""Phase 5 optimiser: minimum lifecycle cost (PV of CAPEX + OPEX) over the concession.

* :func:`optimise_design`: for a fixed alignment, enumerate diameter x grade x
  design pressure (MAOP). Each candidate is fully evaluated (walls per
  location class with optional ALA checks, yearly hydraulics with phased
  stations and offtakes, CAPEX, OPEX, cash flow).
* :func:`optimise_route`: the outer loop on an AOI (plan 6.3):

  1. build the cost surface for the current best diameter and pressure, with
     the OPEX-equivalent length penalty of the current best option;
  2. generate distinct corridors and run :func:`optimise_design` on each;
  3. take the best (corridor, design); stop if lifecycle cost improved by less
     than ``tol`` or the design repeats, otherwise re-route with it.

Every evaluation is recorded in a :class:`ScenarioTree`.
"""

from __future__ import annotations

from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field

from pydantic import Field

from pipeline_analytics.cost.rates import _Model
from pipeline_analytics.economics.option import OptionInput, OptionResult, evaluate_option
from pipeline_analytics.optimiser.scenarios import ScenarioNode, ScenarioTree
from pipeline_analytics.routing.aoi import AOI
from pipeline_analytics.routing.cost_surface import CostSurfaceParams, build_cost_surface
from pipeline_analytics.routing.extract import RouteExtraction, extract_route, smooth_path_polyline
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


@dataclass
class RouteOptimisation:
    best_input: OptionInput
    best: OptionResult
    best_route: RouteExtraction
    iterations: list[RouteIteration]
    tree: ScenarioTree
    converged: bool
    history: list[float] = field(default_factory=list)


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
) -> RouteOptimisation:
    tree = ScenarioTree()
    current = base
    iterations: list[RouteIteration] = []
    history: list[float] = []
    best_overall: tuple[OptionInput, OptionResult, RouteExtraction] | None = None
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
        searches: list[DesignSearch] = []
        for ci, ex in enumerate(exs):
            c_node = tree.add_group(
                f"corridor C{ci + 1}", parent=it_node, iteration=it,
                length_km=round(ex.length_m / 1000, 2),
            )  # fmt: skip
            searches.append(
                optimise_design(
                    ex.option_input(current),
                    space,
                    tree=tree,
                    parent=c_node,
                    iteration=it,
                    label=f"it{it} C{ci + 1}",
                )
            )
        bi = min(range(len(searches)), key=lambda i: searches[i].best.lcc_pv)
        s = searches[bi]
        lcc = s.best.lcc_pv
        iterations.append(RouteIteration(it, exs, bi, s, lcc))
        history.append(lcc)
        if best_overall is None or lcc < best_overall[1].lcc_pv:
            best_overall = (s.best_input, s.best, exs[bi])
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
    )
