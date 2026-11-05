"""Geohazard layers linked into routing: crossings, strain checks, mitigation, realignment."""

import json
import math
from pathlib import Path

import pytest

from pipeline_analytics.core.jurisdiction import Service
from pipeline_analytics.cost import placeholder_rates
from pipeline_analytics.economics import Concession, OptionInput, ramp_profile
from pipeline_analytics.hydraulics import GasStationInput, Profile
from pipeline_analytics.optimiser import DesignSpace, optimise_route
from pipeline_analytics.routing import (
    CostSurfaceParams,
    build_cost_surface,
    extract_route,
    least_cost_path,
    load_geojson_features,
    smooth_path_polyline,
)
from pipeline_analytics.routing.geohazards import (
    Fault,
    Geohazards,
    GroundZone,
    assess_geohazards,
    find_crossings,
    realign_fault_crossing,
)
from pipeline_analytics.routing.sample import END, START, sample_aoi

from .conftest import flat_aoi

ROUTE = [(0.0, 0.0), (10_000.0, 0.0)]


def test_fault_crossing_geometry_and_slip_resolution() -> None:
    # trace at 60 deg to the pipe, digitised so that it dips to the right (hanging wall ahead)
    s = (math.cos(math.radians(60)), math.sin(math.radians(60)))
    trace = ((4000 - 2000 * s[0], -2000 * s[1]), (4000 + 2000 * s[0], 2000 * s[1]))
    f = Fault(name="F", trace=trace, slip_m=2.0)
    (c,) = find_crossings(ROUTE, Geohazards(faults=(f,)))
    assert c.chainage_m == pytest.approx(4000)
    assert c.crossing_angle_deg == pytest.approx(60)
    assert c.movement.axial_m == pytest.approx(1.0)  # left-lateral, 60 deg: tension
    assert c.movement.transverse_m == pytest.approx(math.sqrt(3))
    # digitised the other way the fault dips to the other side: for strike-slip the sense of
    # slip is unchanged, so the movement is too
    rev = f.model_copy(update={"trace": f.trace[::-1]})
    (c2,) = find_crossings(ROUTE, Geohazards(faults=(rev,)))
    assert c2.crossing_angle_deg == pytest.approx(240)
    assert c2.movement.axial_m == pytest.approx(1.0)
    # reverse fault (45 deg dip): shortening either way, but the block ahead goes up when it
    # is the hanging wall and down when it is the footwall
    rv = f.model_copy(update={"rake_deg": 90.0, "dip_deg": 45.0})
    (a,) = find_crossings(ROUTE, Geohazards(faults=(rv,)))
    (b,) = find_crossings(ROUTE, Geohazards(faults=(rv.model_copy(update={"trace": trace[::-1]}),)))
    assert a.movement.axial_m < 0 and b.movement.axial_m < 0
    assert (
        a.movement.vertical_m == pytest.approx(-b.movement.vertical_m) and a.movement.vertical_m > 0
    )
    assert find_crossings([(0, 5000), (10_000, 5000)], Geohazards(faults=(f,))) == []


def test_zone_crossing_width_and_movement() -> None:
    z = GroundZone(name="slide", kind="landslide", displacement_m=1.0, direction_azimuth_deg=0.0,
                   polygon=((3000, -500), (3600, -500), (3600, 500), (3000, 500)))  # fmt: skip
    (c,) = find_crossings(ROUTE, Geohazards(zones=(z,)))
    assert c.width_m == pytest.approx(600, abs=10)
    assert c.chainage_m == pytest.approx(3300, abs=10)
    assert c.movement.transverse_m == pytest.approx(1.0)  # moving north = to the pipe's left
    assert c.movement.axial_m == pytest.approx(0.0, abs=1e-9)
    sub = z.model_copy(update={"kind": "subsidence", "direction_azimuth_deg": None})
    (cs,) = find_crossings(ROUTE, Geohazards(zones=(sub,)))
    assert cs.movement.vertical_m == pytest.approx(-1.0)
    with pytest.raises(ValueError, match="direction"):
        GroundZone(name="x", kind="lateral_spread", displacement_m=1, polygon=z.polygon)


def test_layers_burn_into_hazard_raster() -> None:
    f = Fault(name="F", trace=((2000, 0), (2000, 4000)), slip_m=1.0, zone_half_width_m=250)
    z = GroundZone(name="S", kind="subsidence", displacement_m=0.3,
                   polygon=((4000, 1000), (5000, 1000), (5000, 2000), (4000, 2000)))  # fmt: skip
    aoi = flat_aoi(geohazards=Geohazards(faults=(f,), zones=(z,)))
    assert aoi.hazard is not None
    r, c = aoi.grid.rc(2000, 2000)
    assert aoi.hazard[r, c] == 3 and aoi.hazard[r, c + 4] == 0
    r, c = aoi.grid.rc(4500, 1500)
    assert aoi.hazard[r, c] == 2


def test_geojson_loads_geohazards(tmp_path: Path) -> None:
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"kind": "fault", "name": "Main", "slip_m": 1.5,
                                           "rake_deg": 180},
         "geometry": {"type": "LineString", "coordinates": [[0, 0], [100, 1000]]}},
        {"type": "Feature", "properties": {"kind": "landslide", "name": "LS1",
                                           "displacement_m": 0.8, "direction_azimuth_deg": 135},
         "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [50, 0], [50, 50], [0, 0]]]}},
    ]}  # fmt: skip
    p = tmp_path / "h.geojson"
    p.write_text(json.dumps(fc))
    g = load_geojson_features(p)["geohazards"]
    assert g.faults[0].name == "Main" and g.faults[0].rake_deg == 180
    assert g.zones[0].kind == "landslide" and g.zones[0].direction_azimuth_deg == 135


@pytest.fixture(scope="module")
def left_lateral_sample():  # type: ignore[no-untyped-def]
    aoi = sample_aoi(200.0)
    assert aoi.geohazards is not None
    f = aoi.geohazards.faults[0].model_copy(update={"slip_m": 1.0, "rake_deg": 0.0})
    aoi.geohazards = Geohazards(faults=(f,), zones=aoi.geohazards.zones)
    surf = build_cost_surface(aoi, CostSurfaceParams(service=Service.GAS, nps="24",
                                                     design_pressure_pa_g=92e5))  # fmt: skip
    ex = extract_route(surf, smooth_path_polyline(surf, least_cost_path(surf, START, END).cells))
    return aoi, surf, f, ex


def test_failing_fault_gets_heavier_wall_and_best_angle(left_lateral_sample) -> None:  # type: ignore[no-untyped-def]
    _, _, _, ex = left_lateral_sample
    a = assess_geohazards(ex.geohazard_crossings, nps="24", grade="X70", wall_m=0.0103,
                          pressure_pa=92e5, rates=placeholder_rates())  # fmt: skip
    (chk,) = [c for c in a.checks if c.crossing.kind == "fault"]
    assert not chk.passed and chk.utilisation > 1
    assert chk.mitigation_wall_mm is not None and chk.mitigation_wall_mm > 10.3
    assert chk.mitigation_cost > 0 and a.mitigation_cost == pytest.approx(chk.mitigation_cost)
    assert chk.best_crossing_angle_deg is not None and chk.best_crossing_angle_deg < 90
    assert a.passed and not a.unmitigated  # mitigated


def test_realignment_crosses_at_target_angle(left_lateral_sample) -> None:  # type: ignore[no-untyped-def]
    _, surf, f, ex = left_lateral_sample
    (cr,) = [c for c in ex.geohazard_crossings if c.kind == "fault"]
    line = realign_fault_crossing(surf, START, END, cr, f, 45.0)
    (new,) = [c for c in find_crossings(line, Geohazards(faults=(f,)))]
    assert new.crossing_angle_deg is not None
    assert new.crossing_angle_deg % 180 == pytest.approx(45, abs=0.5)
    cell = surf.grid.cell_m
    assert math.dist(line[0], START) < cell and math.dist(line[-1], END) < cell


def test_optimise_route_weighs_mitigation_against_realignment(left_lateral_sample) -> None:  # type: ignore[no-untyped-def]
    aoi, _, _, _ = left_lateral_sample
    base = OptionInput(
        service=Service.GAS, nps="24", design_pressure_pa_g=92e5, profile=Profile.flat(40e3),
        throughput=ramp_profile(6, 9, 5, 15),
        gas_stations=GasStationInput(supply_pressure_pa_g=70e5, delivery_min_pa_g=30e5),
        concession=Concession(operating_years=15),
    )  # fmt: skip
    space = DesignSpace(sizes=("24",), grades=("X70",), design_pressures_pa_g=(92e5,))
    ro = optimise_route(aoi, START, END, base, space, k=1, max_iterations=1)
    it = ro.iterations[0]
    assert any("realigned" in lb for lb in it.labels)  # variant generated for the bad crossing
    adjusted = [sr.best.lcc_pv + pv for sr, pv in zip(it.searches, it.geohazard_pv, strict=True)]
    assert it.lcc_pv == pytest.approx(min(adjusted))
    assert ro.best_geohazards is not None or it.geohazards[it.best_corridor] is None
    assert it.geohazard_pv[0] > 0  # the original corridor pays for the heavier wall
