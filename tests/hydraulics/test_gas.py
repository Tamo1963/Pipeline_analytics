import itertools
import math

import pytest

from pipeline_analytics.core.units import Q_
from pipeline_analytics.hydraulics import (
    Gas,
    GasHydraulicsResult,
    GasStationInput,
    HydraulicsError,
    Profile,
    compressor_power_w,
    gas_hydraulics,
    gas_pressure_profile,
)
from pipeline_analytics.hydraulics.fluids import R_UNIVERSAL, to_abs
from pipeline_analytics.hydraulics.gas import GasLine

ID = 0.610 - 2 * 0.00953
G = 9.80665


def ode_outlet(
    line: GasLine, p1: float, length: float, dh: float, z: float, n: int = 20000
) -> float:
    """RK4 on dP/dx = -f m^2 Z R T / (2 D A^2 M P) - P M g sin(theta) / (Z R T)."""
    t, m = line.gas.flowing_temperature_k, line.gas.molar_mass
    sin = dh / length
    k_f = (
        line.friction * line.mass_flow**2 * z * R_UNIVERSAL * t / (2 * line.id_m * line.area**2 * m)
    )
    k_g = m * G * sin / (z * R_UNIVERSAL * t)

    def f(p: float) -> float:
        return -k_f / p - k_g * p

    h, p = length / n, p1
    for _ in range(n):
        k1 = f(p)
        k2 = f(p + h / 2 * k1)
        k3 = f(p + h / 2 * k2)
        k4 = f(p + h * k3)
        p += h / 6 * (k1 + 2 * k2 + 2 * k3 + k4)
    return p


@pytest.mark.parametrize("dh", [0.0, 300.0, -300.0])
def test_general_flow_equation_matches_ode(dh: float) -> None:
    prof = Profile(chainage_m=(0, 100e3), elevation_m=(0, dh))
    line = GasLine(prof, ID, 6e6 / 86400, Gas(), z_override=0.9)
    p_closed = line.step(to_abs(70e5), 0)
    assert p_closed == pytest.approx(ode_outlet(line, to_abs(70e5), 100e3, dh, 0.9), rel=1e-4)


def test_flat_closed_form() -> None:
    prof = Profile.flat(100e3)
    line = GasLine(prof, ID, 6e6 / 86400, Gas(), z_override=1.0)
    g = Gas()
    p1 = to_abs(70e5)
    rhs = (
        line.friction
        * line.mass_flow**2
        * R_UNIVERSAL
        * g.flowing_temperature_k
        * 100e3
        / (ID * line.area**2 * g.molar_mass)
    )
    assert line.step(p1, 0) == pytest.approx(math.sqrt(p1**2 - rhs))


def test_mass_flow_from_mmscmd() -> None:
    prof = Profile.flat(1000)
    line = GasLine(prof, ID, Q_(6, "MMSCMD").to("m**3/s").magnitude, Gas())
    assert line.mass_flow == pytest.approx(6e6 / 86400 * Gas().std_density)


def test_efficiency_raises_pressure_drop() -> None:
    prof = Profile.flat(100e3)
    base = gas_pressure_profile(prof, ID, Q_(6, "MMSCMD"), 70e5)[-1]
    eff = gas_pressure_profile(prof, ID, Q_(6, "MMSCMD"), 70e5, efficiency=0.92)[-1]
    assert eff < base
    with pytest.raises(ValueError):
        gas_pressure_profile(prof, ID, 1.0, 70e5, efficiency=1.2)


def test_profile_decreases_and_nan_when_unreachable() -> None:
    prof = Profile.flat(100e3, points=11)
    p = gas_pressure_profile(prof, ID, Q_(6, "MMSCMD"), 70e5)
    assert all(b < a for a, b in itertools.pairwise(p))
    tiny = gas_pressure_profile(prof, 0.1, Q_(6, "MMSCMD"), 70e5)
    assert math.isnan(tiny[-1])


def test_compressor_power_hand_calc() -> None:
    g = Gas()
    s, d = to_abs(40e5), to_abs(60e5)
    k = g.heat_capacity_ratio
    z1 = g.z(s)
    expected = (
        50 * z1 * R_UNIVERSAL * g.flowing_temperature_k / g.molar_mass
        * k / (k - 1) * ((d / s) ** ((k - 1) / k) - 1) / 0.8
    )  # fmt: skip
    assert compressor_power_w(50, s, d, g) == pytest.approx(expected)


ROUTE = Profile(
    chainage_m=(0, 50e3, 100e3, 150e3, 200e3, 250e3, 300e3),
    elevation_m=(50, 120, 300, 250, 180, 90, 60),
).resample(1000)


def run(flow_mmscmd: float, supply: float = 40e5, **kw: float) -> GasHydraulicsResult:
    return gas_hydraulics(
        ROUTE,
        ID,
        Q_(flow_mmscmd, "MMSCMD"),
        GasStationInput(supply_pressure_pa_g=supply, delivery_min_pa_g=30e5, **kw),
        maop_pa_g=92e5,
    )


def test_station_plan_meets_delivery_and_limits() -> None:
    r = run(6)
    assert r.march.delivery_pressure_pa_g == pytest.approx(30e5, rel=1e-6)
    assert all(c.passed for c in r.checks)
    assert len(r.stations) == 1 and r.stations[0].chainage_m == 0
    assert r.stations[0].power_w == pytest.approx(
        compressor_power_w(
            r.mass_flow_kg_s, to_abs(40e5), to_abs(r.stations[0].discharge_pa_g), Gas()
        )
    )
    assert r.fuel_gas_kg_s == pytest.approx(r.total_power_w / (0.33 * 47e6))


def test_more_flow_needs_more_stations_and_power() -> None:
    low, high = run(6), run(15)
    assert len(high.stations) > len(low.stations)
    assert high.total_power_w > low.total_power_w
    assert all(s.ratio <= 1.6 + 1e-9 for s in high.stations)
    assert all(p <= 92e5 + 1 for p in high.march.pressure_pa_g)


def test_no_station_when_supply_is_enough() -> None:
    r = run(2, supply=80e5)
    assert r.stations == ()
    assert r.march.delivery_pressure_pa_g > 30e5


def test_infeasible_raises() -> None:
    with pytest.raises(HydraulicsError):
        gas_hydraulics(
            ROUTE,
            0.15,
            Q_(15, "MMSCMD"),
            GasStationInput(supply_pressure_pa_g=40e5, delivery_min_pa_g=30e5),
            maop_pa_g=92e5,
        )


def test_lower_maop_segment_caps_pressure() -> None:
    n = len(ROUTE.chainage_m)
    maop = tuple(92e5 if x < 100e3 else 60e5 for x in ROUTE.chainage_m)
    r = gas_hydraulics(
        ROUTE,
        ID,
        Q_(10, "MMSCMD"),
        GasStationInput(supply_pressure_pa_g=40e5, delivery_min_pa_g=30e5),
        maop_pa_g=maop,
    )
    assert len(maop) == n
    assert all(p <= m + 1 for p, m in zip(r.march.pressure_pa_g, maop, strict=True))
    assert r.checks[0].passed
