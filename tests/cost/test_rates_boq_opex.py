import math
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from pipeline_analytics.core.jurisdiction import Service
from pipeline_analytics.cost import (
    CrossingType,
    LandUse,
    OpexYear,
    PipeSegmentQty,
    RouteQuantities,
    StationQty,
    Terrain,
    annual_opex,
    capex_estimate,
    crore,
    load_rates,
    placeholder_rates,
)
from pipeline_analytics.cost.boq import sectionalising_valves
from pipeline_analytics.mechanical import LocationClass

R = placeholder_rates()


def test_placeholder_flagged() -> None:
    assert R.placeholder is True
    assert R.currency == "INR" and R.base_year == 2026


def test_pipe_rate_factors() -> None:
    assert R.pipe_rate_per_tonne("X70", "SAWH") == pytest.approx(90_000 * 1.03 * 0.92)
    with pytest.raises(KeyError):
        R.pipe_rate_per_tonne("X99", "SAWL")


def test_load_custom_rates(tmp_path: Path) -> None:
    data = R.model_dump(mode="json")
    data["placeholder"] = False
    data["laying_per_inch_km"] = 150_000
    f = tmp_path / "project.yaml"
    f.write_text(yaml.safe_dump(data))
    custom = load_rates(f)
    assert not custom.placeholder and custom.laying_per_inch_km == 150_000
    data["indirects"]["contingency"] = -0.1
    f.write_text(yaml.safe_dump(data))
    with pytest.raises(ValidationError):
        load_rates(f)


def test_crore() -> None:
    assert crore(1e9) == 100


SEG = PipeSegmentQty(length_m=100_000, od_m=0.610, wt_m=0.0081, grade="X70", process="SAWH")


def test_segment_tonnes() -> None:
    assert SEG.tonnes == pytest.approx(0.0246615 * (610 - 8.1) * 8.1 * 100_000 / 1000, rel=1e-5)


def test_capex_hand_calc() -> None:
    route = RouteQuantities(
        terrain_m={Terrain.HILLY: 20_000},
        land_use_m={LandUse.FOREST: 5_000},
        crossings={CrossingType.NATIONAL_HIGHWAY: 2, CrossingType.RAILWAY: 1},
        hdd_m=600,
        terminals=2,
    )
    est = capex_estimate(Service.GAS, [SEG], route, StationQty(required_kw=10_000, count=1), R)
    amt = {i.description: i.amount for i in est.items}
    od_in = 0.610 / 0.0254
    assert amt["X70 SAWH 610x8.10 mm"] == pytest.approx(SEG.tonnes * 90_000 * 1.03 * 0.92)
    assert amt["3LPE external coating"] == pytest.approx(math.pi * 0.610 * 100_000 * 1000)
    assert amt["laying, hilly terrain"] == pytest.approx(od_in * 20 * 120_000 * 1.8)
    # 100 km - 20 km hilly - 0.6 km HDD = 79.4 km flat (HDD is not also laid open-cut)
    assert amt["laying, flat terrain"] == pytest.approx(od_in * 79.4 * 120_000)
    assert amt["HDD crossings"] == pytest.approx(od_in * 600 * 2000)
    assert amt["national_highway crossings"] == 8e6
    assert amt["compressor stations (fixed)"] == 5e8
    assert amt["compressor power installed"] == pytest.approx(10_000 * 1.5 * 150_000)
    assert amt["right of use, forest"] == pytest.approx(5_000 * 20 / 1e4 * 1.5e6)
    assert amt["right of use, agricultural"] == pytest.approx(94_400 * 20 / 1e4 * 3e5)
    # 100 km Class 1 -> ceil(100/32.187) - 1 = 3 SV stations
    assert amt["sectionalising valve stations"] == 3 * 1.5e7
    d, land = est.direct_excl_land, est.land
    assert est.taxes == pytest.approx(0.18 * d)
    assert est.engineering == pytest.approx(0.07 * d)
    assert est.owners_cost == pytest.approx(0.02 * (d + land))
    sub = d + land + est.taxes + est.engineering + est.owners_cost
    assert est.contingency == pytest.approx(0.10 * sub)
    assert est.total == pytest.approx(sub * 1.10)
    assert sum(est.by_group().values()) == pytest.approx(est.total)
    assert "Class 5" in est.accuracy_class


def test_capex_validation() -> None:
    with pytest.raises(ValueError):
        capex_estimate(Service.GAS, [], RouteQuantities(), StationQty(required_kw=0, count=0), R)
    with pytest.raises(ValueError):
        capex_estimate(
            Service.GAS,
            [SEG],
            RouteQuantities(terrain_m={Terrain.ROCK: 200_000}),
            StationQty(required_kw=0, count=0),
            R,
        )


def test_pump_stations_and_no_stations() -> None:
    liq = capex_estimate(
        Service.LIQUID, [SEG], RouteQuantities(), StationQty(required_kw=5000, count=2), R
    )
    assert any(i.description == "pump stations (fixed)" and i.amount == 4e8 for i in liq.items)
    none = capex_estimate(
        Service.GAS, [SEG], RouteQuantities(), StationQty(required_kw=0, count=0), R
    )
    assert not any(i.group == "stations" for i in none.items)


def test_sv_spacing_by_class() -> None:
    c3 = SEG.model_copy(update={"location_class": LocationClass.CLASS_3})
    assert sectionalising_valves(Service.GAS, [c3]) == math.ceil(100_000 / 16_093) - 1
    assert sectionalising_valves(Service.LIQUID, [SEG]) == math.ceil(100 / 30) - 1
    short = SEG.model_copy(update={"length_m": 10_000})
    assert sectionalising_valves(Service.GAS, [short]) == 0


def test_annual_opex() -> None:
    y = OpexYear(operating_year=5, length_km=300, stations=2, electricity_kwh=1e6,
                 fuel_gas_scm=2e6, station_capex=1e9, cumulative_capex=1e10)  # fmt: skip
    o = annual_opex(y, R)
    assert o.energy == pytest.approx(1e6 * 8 + 2e6 * 25)
    assert o.line_om == pytest.approx(300 * 1.5e5)
    assert o.station_om == pytest.approx(0.03 * 1e9)
    assert o.manpower == pytest.approx(1e8 + 2 * 3e7)
    assert o.inspection == pytest.approx(300 * 1.2e5)  # year 5 = ILI year
    assert o.insurance == pytest.approx(0.003 * 1e10)
    assert annual_opex(y.model_copy(update={"operating_year": 4}), R).inspection == 0
    assert o.total == pytest.approx(
        o.energy + o.line_om + o.station_om + o.manpower + o.inspection + o.insurance
    )


def test_hdd_length_not_double_counted() -> None:
    """Regression: HDD metres were also charged as flat laying and right of use."""
    zero = StationQty(required_kw=0, count=0)
    dry = capex_estimate(Service.GAS, [SEG], RouteQuantities(), zero, R)
    wet = capex_estimate(Service.GAS, [SEG], RouteQuantities(hdd_m=5_000), zero, R)
    d_amt = {i.description: i.amount for i in dry.items}
    w_amt = {i.description: i.amount for i in wet.items}
    od_in = 0.610 / 0.0254
    assert d_amt["laying, flat terrain"] - w_amt["laying, flat terrain"] == pytest.approx(
        od_in * 5 * 120_000
    )
    assert d_amt["right of use, agricultural"] - w_amt["right of use, agricultural"] == (
        pytest.approx(5_000 * 20 / 1e4 * 3e5)
    )
    assert w_amt["HDD crossings"] == pytest.approx(od_in * 5_000 * 2000)
    with pytest.raises(ValueError, match="HDD"):
        capex_estimate(Service.GAS, [SEG], RouteQuantities(hdd_m=200_000), zero, R)


def test_with_indirects_matches_capex_estimate() -> None:
    from pipeline_analytics.cost.boq import with_indirects

    est = capex_estimate(
        Service.GAS, [SEG], RouteQuantities(), StationQty(required_kw=0, count=0), R
    )
    assert with_indirects(est.direct_excl_land, est.land, R) == pytest.approx(est.total)
