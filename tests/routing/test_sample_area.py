"""Phase 4 acceptance on the synthetic 40 x 24 km area of interest."""

import json
import time
from pathlib import Path

import numpy as np
import pytest

from pipeline_analytics.core.jurisdiction import Service
from pipeline_analytics.economics import OptionInput, evaluate_option, ramp_profile
from pipeline_analytics.hydraulics import GasStationInput, Profile
from pipeline_analytics.routing import (
    CostSurfaceParams,
    alternative_corridors,
    aoi_from_layers,
    build_cost_surface,
    extract_route,
    load_geojson_features,
    opex_penalties,
    smooth_path_polyline,
    to_geojson,
    to_kml,
)
from pipeline_analytics.routing.sample import END, START, sample_aoi

BASE = OptionInput(
    service=Service.GAS,
    nps=30,
    design_pressure_pa_g=92e5,
    profile=Profile.flat(38e3),
    throughput=ramp_profile(6, 10, 8, 25),
    gas_stations=GasStationInput(supply_pressure_pa_g=60e5, delivery_min_pa_g=30e5),
)


@pytest.fixture(scope="module")
def corridors():  # type: ignore[no-untyped-def]
    aoi = sample_aoi()
    pen = opex_penalties(BASE)
    s = build_cost_surface(
        aoi,
        CostSurfaceParams(
            service=Service.GAS, nps=30, design_pressure_pa_g=92e5, length_per_m=pen.length_per_m
        ),
    )
    t = time.perf_counter()
    alts = alternative_corridors(s, START, END, k=4)
    elapsed = time.perf_counter() - t
    exs = [extract_route(s, smooth_path_polyline(s, a.cells)) for a in alts]
    return s, alts, exs, elapsed


def test_three_plus_distinct_corridors_under_two_minutes(corridors) -> None:  # type: ignore[no-untyped-def]
    _, alts, _, elapsed = corridors
    assert len(alts) >= 3
    assert elapsed < 120


def test_corridors_respect_exclusions_and_count_crossings(corridors) -> None:  # type: ignore[no-untyped-def]
    _, _, exs, _ = corridors
    # manual check of the sample layout for the best corridor:
    # two streams, one each of national highway, state highway and railway
    assert exs[0].crossings == {"river": 2, "national_highway": 1, "state_highway": 1, "railway": 1}
    for ex in exs:
        assert ex.protected_m == 0
        assert ex.crossings["river"] == 2 and ex.crossings["railway"] == 1
        assert ex.crossings["state_highway"] == 1
        # start is north of the highway and the end south of it: an odd number of crossings
        assert ex.crossings["national_highway"] % 2 == 1
        assert ex.hdd_m == pytest.approx(400, abs=50)  # 400 m river, crossed square-on
        assert ex.hazard_m.get(3, 0) > 0  # the fault zone spans the area: must be crossed
        assert 38_000 < ex.length_m < 52_000


def test_alternatives_are_genuinely_different(corridors) -> None:  # type: ignore[no-untyped-def]
    """Default buffer gives a northern (saddle) and a southern option, not parallel lanes."""
    s, alts, _, _ = corridors
    rows = [[r for r, _ in a.cells] for a in alts]
    north_km = min(min(r) for r in rows) * s.grid.cell_m / 1000
    south_km = max(max(r) for r in rows) * s.grid.cell_m / 1000
    assert north_km < 6.0  # reaches towards the saddle (start/end are at 12 km)
    assert south_km > 14.0


def test_corridors_avoid_the_town(corridors) -> None:  # type: ignore[no-untyped-def]
    _, _, exs, _ = corridors
    best = exs[0]
    assert all(sg.location_class.value == "1-2" for sg in best.class_segments)


def test_corridors_evaluate_end_to_end(corridors) -> None:  # type: ignore[no-untyped-def]
    _, _, exs, _ = corridors
    results = [evaluate_option(ex.option_input(BASE)) for ex in exs]
    assert all(r.feasible for r in results)
    assert all(np.isfinite(r.lcc_pv) for r in results)


def test_penalties_hand_calc() -> None:
    pen = opex_penalties(BASE)
    r = 0.10
    pvf = sum((1 + r) ** -(3 + t) for t in range(1, 26))
    assert pen.pv_factor == pytest.approx(pvf)
    price = 25.0 / (0.33 * 47e6 * BASE.gas.std_density)
    assert pen.price_per_joule == pytest.approx(price)
    assert pen.lift_per_m_net == pytest.approx(
        pen.mass_flow_kg_s * 9.80665 / 0.80 * price * 3600 * 8760 * pvf
    )
    assert pen.length_per_m > 150 * pvf  # at least the line O&M per metre


def test_export(corridors) -> None:  # type: ignore[no-untyped-def]
    s, _, exs, _ = corridors
    items = [(f"C{i + 1}", ex.polyline, {"length_km": round(ex.length_m / 1000, 1)})
             for i, ex in enumerate(exs)]  # fmt: skip
    gj = json.loads(to_geojson(items, s.grid.crs))
    assert gj["crs"]["properties"]["name"] == "EPSG:32643"
    assert len(gj["features"]) == len(exs)
    kml = to_kml(items[:1], s.grid.crs)
    coords = kml.split("<coordinates>")[1].split("</coordinates>")[0].split()
    lon, lat, _ = map(float, coords[0].split(","))
    assert 72.9 < lon < 73.2 and 22.4 < lat < 22.8


def test_geojson_loader_round_trip(tmp_path: Path) -> None:
    aoi = sample_aoi(cell_m=200)
    fc = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"kind": "railway"},
             "geometry": {"type": "LineString",
                          "coordinates": [[302000, 2512000], [302000, 2488000]]}},
            {"type": "Feature", "properties": {"kind": "protected"},
             "geometry": {"type": "Polygon", "coordinates": [[[310000, 2505000], [312000, 2505000],
                                                              [312000, 2503000], [310000, 2503000],
                                                              [310000, 2505000]]]}},
            {"type": "Feature", "properties": {"kind": "building", "dwelling_units": 4},
             "geometry": {"type": "Point", "coordinates": [305000, 2500000]}},
        ],
    }  # fmt: skip
    f = tmp_path / "features.geojson"
    f.write_text(json.dumps(fc))
    feats = load_geojson_features(f)
    new = aoi_from_layers(aoi.grid, aoi.elevation_m, aoi.land_cover, feats)
    assert new.line_mask("railway").sum() == aoi.grid.nrows
    assert new.layer("protected").sum() == 10 * 10
    assert new.buildings[0].dwelling_units == 4
    with pytest.raises(ValueError):
        aoi_from_layers(aoi.grid, aoi.elevation_m, aoi.land_cover,
                        {"lines": {}, "polygons": {"bogus": []}, "buildings": []})  # fmt: skip
