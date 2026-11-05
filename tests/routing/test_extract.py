import numpy as np
import pytest

from pipeline_analytics.core.jurisdiction import Service
from pipeline_analytics.cost import CrossingType, LandUse, Terrain
from pipeline_analytics.economics import OptionInput, evaluate_option, ramp_profile
from pipeline_analytics.hydraulics import GasStationInput, Profile
from pipeline_analytics.mechanical import LocationClass
from pipeline_analytics.routing import (
    AOI,
    RIVER,
    BuildingPoint,
    CostSurfaceParams,
    LandCover,
    build_cost_surface,
    extract_route,
    least_cost_path,
    smooth_path_polyline,
)
from pipeline_analytics.routing.extract import simplify

from .conftest import flat_aoi

LINE = [(250.0, 2050.0), (5750.0, 2050.0)]


def layered_aoi() -> AOI:
    aoi = flat_aoi(
        water=np.zeros((40, 60), bool),
        lines={RIVER: [[(1000.0, 0.0), (1000.0, 4000.0)]],
               "railway": [[(2000.0, 0.0), (2400.0, 4000.0)]]},
        buildings=[BuildingPoint(4000.0 + i * 10, 2100.0) for i in range(50)],
    )  # fmt: skip
    assert aoi.water is not None
    aoi.water[:, 45:48] = True  # 300 m wide river polygon
    aoi.land_cover[:, 10:20] = LandCover.FOREST  # 1 km of forest
    aoi.land_cover[:, 30:35] = LandCover.WETLAND  # 500 m wetland
    aoi.elevation_m[:, :] = np.arange(60) * 1.0
    return aoi


def test_quantities_on_straight_line(gas_params: CostSurfaceParams) -> None:
    s = build_cost_surface(layered_aoi(), gas_params)
    ex = extract_route(s, LINE)
    assert ex.length_m == pytest.approx(5500)
    assert ex.hdd_m == pytest.approx(300)
    assert sum(ex.terrain_m.values()) == pytest.approx(5500 - 300)
    assert sum(ex.land_use_m.values()) == pytest.approx(5500 - 300)
    assert ex.terrain_m[Terrain.FOREST] == pytest.approx(1000)
    assert ex.terrain_m[Terrain.MARSH] == pytest.approx(500)
    assert ex.land_use_m[LandUse.FOREST] == pytest.approx(1000)
    assert ex.wet_length_m == pytest.approx(500)
    assert ex.crossings == {RIVER: 1, "railway": 1}
    assert ex.route.crossings == {CrossingType.RAILWAY: 1}
    assert ex.route.open_cut_river_m == pytest.approx(30)
    assert ex.profile.chainage_m[-1] == pytest.approx(5500)
    assert ex.profile.elevation_m[-1] == pytest.approx(57.0)
    # 50 houses within 200 m -> Class 3 around chainage ~3.9-4.4 km
    c3 = [sg for sg in ex.class_segments if sg.location_class is LocationClass.CLASS_3]
    assert c3 and c3[0].start_m == pytest.approx(3750 - 201, abs=60)


def test_option_input_and_evaluation(gas_params: CostSurfaceParams) -> None:
    s = build_cost_surface(layered_aoi(), gas_params)
    ex = extract_route(s, LINE)
    base = OptionInput(
        service=Service.GAS,
        nps=24,
        design_pressure_pa_g=92e5,
        profile=Profile.flat(1000),
        throughput=ramp_profile(3, 5, 3, 25),
        gas_stations=GasStationInput(supply_pressure_pa_g=70e5, delivery_min_pa_g=30e5),
    )
    inp = ex.option_input(base)
    assert inp.profile.length_m == pytest.approx(5500)
    assert inp.wet_length_m == pytest.approx(500)
    r = evaluate_option(inp)
    assert r.feasible
    assert {w.location_class for w in r.walls} >= {LocationClass.CLASS_3}
    assert r.concrete_coating_m3 > 0
    assert any(i.description == "HDD crossings" for i in r.capex.items)  # type: ignore[union-attr]


def test_simplify() -> None:
    pts = [(float(i), 0.0) for i in range(10)]
    assert simplify(pts, 0.1) == [(0.0, 0.0), (9.0, 0.0)]
    corner = [(0.0, 0.0), (5.0, 0.0), (5.0, 5.0)]
    assert simplify(corner, 0.5) == corner


def test_smoothing_never_crosses_exclusions(gas_params: CostSurfaceParams) -> None:
    wall = np.zeros((40, 60), bool)
    wall[5:40, 30] = True
    s = build_cost_surface(flat_aoi(protected=wall), gas_params)
    p = least_cost_path(s, LINE[0], LINE[1])
    pl = smooth_path_polyline(s, p.cells, tolerance_cells=5)
    ex = extract_route(s, pl)
    assert ex.protected_m == 0
    assert len(pl) < len(p.cells)


def test_zero_length_rejected(gas_params: CostSurfaceParams) -> None:
    s = build_cost_surface(flat_aoi(), gas_params)
    with pytest.raises(ValueError):
        extract_route(s, [(100.0, 100.0), (100.0, 100.0)])
