"""End-to-end option evaluation and the economic-diameter curve (plan Phase 3 acceptance)."""

import math

import pytest

from pipeline_analytics.codes.class_location import Building, classify_route
from pipeline_analytics.core.jurisdiction import Service
from pipeline_analytics.economics import (
    Concession,
    OptionInput,
    best_option,
    economic_diameter_curve,
    evaluate_option,
    option_tornado,
    ramp_profile,
)
from pipeline_analytics.hydraulics import GasStationInput, Liquid, Profile, PumpStationInput
from pipeline_analytics.mechanical import SegmentInput, Soil, SoilType

PROFILE = Profile(
    chainage_m=(0, 50e3, 100e3, 150e3, 200e3, 250e3, 300e3),
    elevation_m=(50, 120, 300, 250, 180, 90, 60),
).resample(5000)
CONC = Concession(operating_years=20)
GAS = OptionInput(
    service=Service.GAS,
    nps=30,
    design_pressure_pa_g=92e5,
    profile=PROFILE,
    throughput=ramp_profile(6, 10, 8, 20),
    gas_stations=GasStationInput(supply_pressure_pa_g=40e5, delivery_min_pa_g=30e5),
    concession=CONC,
)


def test_ramp_profile() -> None:
    p = ramp_profile(6, 10, 5, 8)
    assert p == (6, 7, 8, 9, 10, 10, 10, 10)
    with pytest.raises(ValueError):
        ramp_profile(1, 2, 0, 5)


@pytest.fixture(scope="module")
def curve() -> list:  # type: ignore[type-arg]
    return economic_diameter_curve(GAS, [20, 24, 28, 30, 36, 42])


def test_economic_diameter_u_shape(curve: list) -> None:  # type: ignore[type-arg]
    feasible = [r for r in curve if r.feasible]
    assert not curve[0].feasible and "velocity" in curve[0].issues[0]  # 20" too small
    capex = [r.pv_capex for r in feasible]
    opex = [r.pv_opex for r in feasible]
    lcc = [r.lcc_pv for r in feasible]
    assert opex == sorted(opex, reverse=True)  # bigger pipe, less energy and fewer stations
    assert feasible[-1].pv_capex > min(capex)  # and eventually more capital
    i = lcc.index(min(lcc))
    assert 0 < i < len(lcc) - 1, "optimum must be an interior diameter (U-shaped curve)"
    assert best_option(curve) is feasible[i]


def test_wall_and_tonnage(curve: list) -> None:  # type: ignore[type-arg]
    r30 = next(r for r in curve if r.nps == "30")
    w = r30.walls[0]
    assert w.wt_m == pytest.approx(math.ceil(92e5 * 0.762 / (2 * 0.72 * 485e6) * 1e4) / 1e4)
    assert w.governing == "hoop stress"
    assert w.process == "SAWH"  # cheapest process that can make 30"
    assert r30.pipe_tonnes == pytest.approx(
        0.0246615 * (762 - w.wt_m * 1000) * w.wt_m * 1000 * 300, rel=1e-4
    )


def test_cashflow_consistency(curve: list) -> None:  # type: ignore[type-arg]
    r = next(r for r in curve if r.nps == "30")
    cf = r.cashflow
    assert cf.years[0].capex == pytest.approx(r.capex.total * 0.25)
    assert cf.years[3].throughput == pytest.approx(6e6 * 365)
    assert r.levelised_cost_per_mmbtu == pytest.approx(r.levelised_cost * 252_000 / 9500)
    t = cf.tariff_for_return(0.12)
    assert cf.irr(t) == pytest.approx(0.12, abs=1e-6)
    assert r.capex.placeholder_rates


def test_station_phasing_with_ramp(curve: list) -> None:  # type: ignore[type-arg]
    r = next(r for r in curve if r.nps == "24")
    counts = [h.stations for h in r.hydraulics]
    assert counts[-1] > counts[0]  # stations added as throughput ramps
    extra = [y for y in r.cashflow.years if y.operating and y.capex > 0]
    assert extra, "later stations appear as sustaining CAPEX"


def test_class_location_thickens_wall() -> None:
    houses = [Building(chainage_m=100e3 + i * 10, offset_m=50) for i in range(60)]
    segs = tuple(classify_route(PROFILE.length_m, houses, step_m=100))
    r = evaluate_option(GAS.model_copy(update={"class_segments": segs}))
    by_class = {w.location_class.value: w for w in r.walls}
    assert by_class["3"].wt_m > by_class["1-2"].wt_m
    assert sum(w.length_m for w in r.walls) == pytest.approx(PROFILE.length_m)


def test_ala_burial_and_buoyancy() -> None:
    soil = Soil(soil_type=SoilType.DENSE_SAND, unit_weight_n_m3=18e3, friction_angle_deg=36)
    burial = SegmentInput(service=Service.GAS, design_pressure_pa=92e5, cover_m=1.2, soil=soil)
    r = evaluate_option(GAS.model_copy(update={"burial": burial, "wet_length_m": 5000}))
    assert r.feasible
    assert r.concrete_coating_m3 > 0
    assert r.capex is not None
    assert any("concrete weight coating" in i.description for i in r.capex.items)


def test_ala_can_govern_wall() -> None:
    # very shallow cover under heavy traffic with soft bedding: ring buckling/deflection governs
    soil = Soil(soil_type=SoilType.SOFT_CLAY, unit_weight_n_m3=20e3, cohesion_pa=20e3)
    burial = SegmentInput(
        service=Service.GAS,
        design_pressure_pa=92e5,
        cover_m=0.6,
        soil=soil,
        modulus_of_soil_reaction_pa=0.35e6,
        live_load_pressure_pa=150e3,
    )
    plain = evaluate_option(GAS.model_copy(update={"nps": 42, "design_pressure_pa_g": 40e5}))
    ala = evaluate_option(
        GAS.model_copy(update={"nps": 42, "design_pressure_pa_g": 40e5, "burial": burial})
    )
    assert ala.walls[0].wt_m > plain.walls[0].wt_m
    assert ala.walls[0].governing.startswith("ALA")


def test_burial_template_pressure_is_overridden(monkeypatch: pytest.MonkeyPatch) -> None:
    """The ALA checks must run at the option's design pressure and class, not the template's."""
    from pipeline_analytics.economics import option as option_mod

    seen: list[SegmentInput] = []
    real = option_mod.assess_segment  # type: ignore[attr-defined]

    def spy(pipe: object, seg: SegmentInput) -> object:
        seen.append(seg)
        return real(pipe, seg)  # type: ignore[arg-type]

    monkeypatch.setattr(option_mod, "assess_segment", spy)
    soil = Soil(soil_type=SoilType.DENSE_SAND, unit_weight_n_m3=18e3, friction_angle_deg=36)
    low = SegmentInput(service=Service.LIQUID, design_pressure_pa=1e5, cover_m=1.2, soil=soil)
    evaluate_option(GAS.model_copy(update={"burial": low}))
    assert seen
    assert all(s.design_pressure_pa == 92e5 and s.service is Service.GAS for s in seen)


def test_liquid_option() -> None:
    liq = OptionInput(
        service=Service.LIQUID,
        nps=16,
        design_pressure_pa_g=90e5,
        profile=PROFILE,
        throughput=(5.0,) * 20,
        liquid=Liquid(),
        pump_stations=PumpStationInput(),
        concession=CONC,
    )
    r = evaluate_option(liq)
    assert r.feasible
    assert r.throughput_unit == "t"
    assert r.hydraulics[0].electricity_kwh == pytest.approx(r.hydraulics[0].average_power_kw * 8760)
    assert r.opex[0].energy == pytest.approx(r.hydraulics[0].electricity_kwh * 8.0)


def test_electric_compressors_use_electricity() -> None:
    r = evaluate_option(GAS.model_copy(update={"nps": 24, "compressor_driver": "electric"}))
    h = r.hydraulics[0]
    assert h.fuel_gas_scm == 0 and h.electricity_kwh > 0


def test_throughput_length_validated() -> None:
    with pytest.raises(ValueError):
        evaluate_option(GAS.model_copy(update={"throughput": (6.0,) * 5}))


def test_option_tornado() -> None:
    small = GAS.model_copy(
        update={"profile": Profile.flat(100e3, points=11), "throughput": (6.0,) * 20}
    )
    rows = option_tornado(small, 0.2)
    names = [r[0] for r in rows]
    assert set(names) == {
        "line pipe price",
        "construction rate",
        "station cost",
        "energy price",
        "throughput",
        "discount rate",
    }
    d = dict((r[0], r[1:]) for r in rows)
    assert d["line pipe price"][0] < d["line pipe price"][1]  # dearer steel, dearer transport
    assert d["throughput"][0] > d["throughput"][1]  # more volume spreads fixed cost
    base = evaluate_option(small).levelised_cost
    assert d["line pipe price"][0] < base < d["line pipe price"][1]
    with pytest.raises(KeyError):
        option_tornado(small, parameters=["nonsense"])


def test_option_input_json_round_trip() -> None:
    """Saved studies must reload: inf limits (e.g. no discharge cap) survive JSON."""
    inp = GAS.model_copy(
        update={"gas_stations": GasStationInput(supply_pressure_pa_g=40e5, delivery_min_pa_g=30e5)}
    )
    back = OptionInput.model_validate_json(inp.model_dump_json())
    assert back == inp
    assert math.isinf(back.gas_stations.max_discharge_pa_g)  # type: ignore[union-attr]


def test_offtake_and_declining_supply_in_option() -> None:
    from pipeline_analytics.hydraulics import Offtake

    flat = Profile.flat(150e3, points=31)
    base = GAS.model_copy(update={"profile": flat, "nps": 24, "throughput": (6.0,) * 20})
    falling = tuple(70e5 - 2e5 * t for t in range(20))  # 70 -> 32 barg
    r = evaluate_option(
        OptionInput.model_validate(
            {
                **base.model_dump(),
                "supply_pressure_by_year": falling,
                "offtakes": [Offtake(chainage_m=60e3, share=0.3, min_pressure_pa_g=30e5)],
            }
        )
    )
    assert r.feasible
    powers = [h.average_power_kw for h in r.hydraulics]
    assert powers[-1] > powers[0]  # compression grows as the field pressure falls
    with pytest.raises(ValueError):
        evaluate_option(
            OptionInput.model_validate(
                {**base.model_dump(), "supply_pressure_by_year": (70e5,) * 5}
            )
        )


def test_zero_throughput_years_cost_no_energy() -> None:
    flat = Profile.flat(100e3, points=11)
    r = evaluate_option(
        GAS.model_copy(update={"profile": flat, "throughput": (6.0,) * 15 + (0.0,) * 5})
    )
    assert r.feasible
    assert r.hydraulics[-1].average_power_kw == 0 and r.hydraulics[-1].fuel_gas_scm == 0
