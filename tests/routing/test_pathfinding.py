import math

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from scipy.sparse.csgraph import dijkstra

from pipeline_analytics.core.jurisdiction import Service
from pipeline_analytics.routing import (
    CostSurfaceParams,
    alternative_corridors,
    build_cost_surface,
    least_cost_path,
    path_cost,
)
from pipeline_analytics.routing.pathfinding import _graph, overlap

from .conftest import flat_aoi

A, B = (250.0, 2050.0), (5750.0, 2050.0)


def test_uniform_field_goes_straight(gas_params: CostSurfaceParams) -> None:
    s = build_cost_surface(flat_aoi(), gas_params)
    p = least_cost_path(s, A, B)
    assert {r for r, _ in p.cells} == {s.grid.rc(*A)[0]}  # same row throughout
    assert p.cost == pytest.approx(5500 * s.cell_cost[0, 0])


def test_path_cost_matches_dijkstra(gas_params: CostSurfaceParams) -> None:
    aoi = flat_aoi()
    aoi.elevation_m[:, :] = np.random.default_rng(1).uniform(0, 30, aoi.grid.shape)
    s = build_cost_surface(
        aoi, gas_params.model_copy(update={"climb_per_m": 1e4, "length_per_m": 500})
    )
    p = least_cost_path(s, A, B)
    g = s.grid
    a, b = g.rc(*A), g.rc(*B)
    dist = dijkstra(_graph(s, s.cell_cost), directed=True, indices=a[0] * g.ncols + a[1])
    assert p.cost == pytest.approx(dist[b[0] * g.ncols + b[1]])


def test_exclusion_gap_and_block(gas_params: CostSurfaceParams) -> None:
    wall = np.zeros((40, 60), bool)
    wall[:, 30] = True
    wall[35, 30] = False  # single gap in the south
    s = build_cost_surface(flat_aoi(protected=wall), gas_params)
    p = least_cost_path(s, A, B)
    assert (35, 30) in p.cells
    wall[35, 30] = False
    wall[:, 30] = True
    blocked = build_cost_surface(flat_aoi(protected=wall), gas_params)
    with pytest.raises(ValueError, match="no feasible path"):
        least_cost_path(blocked, A, B)


@settings(max_examples=25, deadline=None)
@given(st.floats(-60, 60))
def test_road_crossed_exactly_once_at_any_angle(angle_deg: float) -> None:
    """A road at any angle is paid exactly once (4-connected raster vs 8-connected path)."""
    t = math.tan(math.radians(angle_deg))
    road = [(3000 - 3000 * t, -1000.0), (3000 + 3000 * t, 5000.0)]
    aoi = flat_aoi(lines={"national_highway": [road]})
    s = build_cost_surface(
        aoi, CostSurfaceParams(service=Service.GAS, nps=24, design_pressure_pa_g=92e5)
    )
    p = least_cost_path(s, A, B)
    m = aoi.line_mask("national_highway")
    on = [bool(m[r, c]) for r, c in p.cells]
    entries = sum(1 for prev, cur in zip([False, *on[:-1]], on, strict=True) if cur and not prev)
    assert entries == 1
    no_road = build_cost_surface(flat_aoi(), s.params)
    assert p.cost >= least_cost_path(no_road, A, B).cost + s.entry_cost["national_highway"] - 1


def test_waypoint_is_visited(gas_params: CostSurfaceParams) -> None:
    s = build_cost_surface(flat_aoi(), gas_params)
    wp = (3050.0, 550.0)
    p = least_cost_path(s, A, B, waypoints=[wp])
    assert s.grid.rc(*wp) in p.cells


def test_climb_heuristic_avoids_hill(gas_params: CostSurfaceParams) -> None:
    aoi = flat_aoi()
    xs, ys = aoi.grid.centers()
    aoi.elevation_m[:, :] = 200 * np.exp(-(((xs - 3000) / 600) ** 2 + ((ys - 2000) / 1200) ** 2))
    plain = least_cost_path(build_cost_surface(aoi, gas_params), A, B)
    heur = least_cost_path(
        build_cost_surface(aoi, gas_params.model_copy(update={"climb_per_m": 1e6})), A, B
    )
    z = aoi.elevation_m
    assert max(z[r, c] for r, c in heur.cells) < max(z[r, c] for r, c in plain.cells)


def test_alternatives_distinct_and_sorted(gas_params: CostSurfaceParams) -> None:
    s = build_cost_surface(flat_aoi(), gas_params)
    alts = alternative_corridors(s, A, B, k=3, buffer_m=500, max_overlap=0.5)
    assert len(alts) == 3
    assert [a.cost for a in alts] == sorted(a.cost for a in alts)
    assert alts[0].cost == pytest.approx(least_cost_path(s, A, B).cost)
    for i, a in enumerate(alts[1:], start=1):
        assert overlap(a, alts[:i], s.grid.shape, 5) <= 0.5 + 1e-9
    assert all(a.cost == pytest.approx(path_cost(s, a.cells)) for a in alts)
