import math

import numpy as np
import pytest

from pipeline_analytics.core.jurisdiction import Service
from pipeline_analytics.cost import placeholder_rates
from pipeline_analytics.routing import (
    RIVER,
    BuildingPoint,
    CostSurfaceParams,
    LandCover,
    build_cost_surface,
)
from pipeline_analytics.routing.cost_surface import TERRAINS

from .conftest import flat_aoi

R = placeholder_rates()
FACTOR = (1 + 0.18 + 0.07 + 0.02) * 1.10
OD = 0.610
OD_IN = OD / 0.0254


def pipe_c1() -> float:
    wt = math.ceil(92e5 * OD / (2 * 0.72 * 485e6) / 1e-4) * 1e-4  # 8.1 mm, D/t ok
    tpm = math.pi * 7850 * (OD - wt) * wt / 1000
    return tpm * 90_000 * 1.03 * 0.92 + math.pi * OD * 1000  # HSAW cheapest + 3LPE


def test_open_flat_cell_hand_calc(gas_params: CostSurfaceParams) -> None:
    s = build_cost_surface(flat_aoi(), gas_params)
    laying = 120_000 / 1000 * OD_IN
    land = 20 * 3e5 / 1e4
    systems = (2e5 + 8e5) / 1000
    assert s.pipe_cost_per_m[1] == pytest.approx(pipe_c1())
    assert s.cell_cost[0, 0] == pytest.approx(FACTOR * (pipe_c1() + laying + land + systems))
    assert s.indirect_factor == pytest.approx(FACTOR)


def test_entry_costs(gas_params: CostSurfaceParams) -> None:
    s = build_cost_surface(flat_aoi(), gas_params)
    assert s.entry_cost["railway"] == pytest.approx(FACTOR * 6e6)
    assert s.entry_cost[RIVER] == pytest.approx(FACTOR * 800 * OD_IN * 30)


def test_layers_change_cost(gas_params: CostSurfaceParams) -> None:
    aoi = flat_aoi()
    base = build_cost_surface(aoi, gas_params).cell_cost[0, 0]
    prot = np.zeros(aoi.grid.shape, bool)
    prot[0, 0] = True
    water = np.zeros(aoi.grid.shape, bool)
    water[0, 1] = True
    corr = np.zeros(aoi.grid.shape, bool)
    corr[0, 2] = True
    haz = np.zeros(aoi.grid.shape, int)
    haz[0, 3] = 3
    s = build_cost_surface(
        flat_aoi(protected=prot, water=water, corridor=corr, hazard=haz), gas_params
    )
    c = s.cell_cost
    assert np.isinf(c[0, 0])
    hdd = 2000 * OD_IN
    systems = 1000.0
    assert c[0, 1] == pytest.approx(FACTOR * (pipe_c1() + hdd + systems))  # no land over water
    laying, land = 120 * OD_IN, 600.0
    assert c[0, 2] == pytest.approx(FACTOR * (pipe_c1() + 0.85 * (laying + land) + systems))
    assert c[0, 3] == pytest.approx(FACTOR * (pipe_c1() + 2.0 * laying + land + systems))
    pen = build_cost_surface(
        flat_aoi(protected=prot), gas_params.model_copy(update={"protected_policy": "penalty"})
    )
    assert pen.cell_cost[0, 0] == pytest.approx(5 * base)


def test_slope_and_land_cover_terrain(gas_params: CostSurfaceParams) -> None:
    aoi = flat_aoi()
    aoi.elevation_m[:, :] = np.arange(aoi.grid.ncols) * 10.0  # 10 % slope eastwards
    aoi.land_cover[5, 5] = LandCover.URBAN
    s = build_cost_surface(aoi, gas_params)
    assert TERRAINS[s.terrain[10, 10]].value == "rolling"
    assert TERRAINS[s.terrain[5, 5]].value == "urban"
    steep = flat_aoi()
    steep.elevation_m[:, :] = np.arange(steep.grid.ncols) * 70.0  # 70 % > max slope
    assert np.isinf(build_cost_surface(steep, gas_params).cell_cost).all()


def test_settlement_density_raises_gas_pipe_cost(gas_params: CostSurfaceParams) -> None:
    town = [BuildingPoint(3000.0 + i % 10 * 20, 2000.0 + i // 10 * 20, 1) for i in range(100)]
    aoi = flat_aoi(buildings=town)
    s = build_cost_surface(aoi, gas_params)
    r, c = aoi.grid.rc(3100, 2100)
    assert s.class_proxy[r, c] == 3 and s.class_proxy[0, 0] == 1
    assert s.pipe_cost_per_m[3] > s.pipe_cost_per_m[2] > s.pipe_cost_per_m[1]
    liq = build_cost_surface(
        aoi, CostSurfaceParams(service=Service.LIQUID, nps=24, design_pressure_pa_g=92e5)
    )
    assert int(np.max(liq.class_proxy)) == 1  # no location classes for liquids
