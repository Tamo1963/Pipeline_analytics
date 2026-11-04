import pytest

from pipeline_analytics.core.units import Q_
from pipeline_analytics.hydraulics import (
    HydraulicsError,
    Liquid,
    Profile,
    PumpStationInput,
    liquid_hydraulics,
    liquid_pressure_profile,
    pump_power_w,
)
from pipeline_analytics.hydraulics.friction import colebrook
from pipeline_analytics.hydraulics.liquid import volumetric_flow_m3_s

G = 9.80665
LIQ = Liquid(density_kg_m3=850, kinematic_viscosity_m2_s=10e-6, vapour_pressure_pa_abs=50e3)


def test_flow_conversions() -> None:
    assert volumetric_flow_m3_s(Q_(5, "MMTPA"), LIQ) == pytest.approx(5e9 / (365.25 * 86400) / 850)
    assert volumetric_flow_m3_s(Q_(100_000, "bpd"), LIQ) == pytest.approx(0.184017, rel=1e-4)
    assert volumetric_flow_m3_s(0.2, LIQ) == 0.2


def test_single_segment_hand_calc() -> None:
    d, q, length, dz = 0.4, 0.2, 20e3, 50.0
    prof = Profile(chainage_m=(0, length), elevation_m=(0, dz))
    v = q / (3.141592653589793 / 4 * d**2)
    re = v * d / 10e-6
    f = colebrook(re, 45.7e-6 / d)
    expected = 60e5 - 850 * G * dz - f * length / d * 850 * v**2 / 2
    assert liquid_pressure_profile(prof, d, q, 60e5, LIQ)[-1] == pytest.approx(expected)


ROUTE = Profile(
    chainage_m=(0, 50e3, 100e3, 150e3, 200e3, 250e3, 300e3),
    elevation_m=(50, 120, 300, 250, 180, 90, 60),
).resample(1000)


def test_station_plan() -> None:
    r = liquid_hydraulics(
        ROUTE, 0.4, Q_(5, "MMTPA"), PumpStationInput(max_discharge_pa_g=90e5), 90e5, LIQ
    )
    assert len(r.stations) == 2
    assert all(c.passed for c in r.checks)
    assert r.march.delivery_pressure_pa_g == pytest.approx(2e5, rel=1e-6)
    s = r.stations[0]
    assert s.power_w == pytest.approx(pump_power_w(r.flow_m3_s, s.discharge_pa_g - s.suction_pa_g))


def test_bigger_pipe_needs_less_power() -> None:
    small = liquid_hydraulics(ROUTE, 0.4, Q_(5, "MMTPA"), PumpStationInput(), 90e5, LIQ)
    big = liquid_hydraulics(ROUTE, 0.5, Q_(5, "MMTPA"), PumpStationInput(), 90e5, LIQ)
    assert big.total_power_w < small.total_power_w


def test_slack_line_over_peak_needs_pressure() -> None:
    """A tall peak forces extra discharge to stay above vapour pressure + margin."""
    peak = Profile(chainage_m=(0, 10e3, 20e3), elevation_m=(0, 500, 0)).resample(500)
    r = liquid_hydraulics(peak, 0.4, 0.15, PumpStationInput(delivery_min_pa_g=1e5), 120e5, LIQ)
    i = peak.chainage_m.index(10e3)
    slack = 50e3 - 101_325 + 1e5
    assert r.march.pressure_pa_g[i] >= slack - 1
    # without the peak the pump would only need delivery + friction
    assert r.stations[0].discharge_pa_g > 850 * G * 500


def test_downhill_maop_violation_reported() -> None:
    down = Profile(chainage_m=(0, 10e3), elevation_m=(1000, 0)).resample(1000)
    r = liquid_hydraulics(down, 0.4, 0.05, PumpStationInput(supply_pressure_pa_g=10e5), 50e5, LIQ)
    assert not r.checks[0].passed  # static head gain exceeds MAOP at the low point


def test_infeasible() -> None:
    with pytest.raises(HydraulicsError):
        liquid_hydraulics(ROUTE, 0.1, Q_(5, "MMTPA"), PumpStationInput(), 90e5, LIQ)


def test_velocity_check() -> None:
    r = liquid_hydraulics(Profile.flat(10e3), 0.2, 0.15, PumpStationInput(), 150e5, LIQ)
    assert not r.checks[-1].passed
