"""Steady-state gas pipeline hydraulics.

General flow equation, derived for steady isothermal flow with kinetic energy
neglected and an elevation correction. Per segment of length L and rise dh:

    P1^2 - e^s P2^2 = f m_dot^2 Z R T L_e / (D A^2 M)
    s   = 2 g M dh / (Z R T)
    L_e = L (e^s - 1) / s

where f is the Darcy friction factor (Colebrook) divided by E^2 for a pipeline
efficiency E, and Z is taken at the segment's average pressure
``(2/3)(P1 + P2 - P1 P2 / (P1 + P2))``. This is the same equation as the
textbook General Flow Equation in volumetric units; Panhandle and Weymouth
are empirical forms of its friction factor.

Compressor power (polytropic approximated as isentropic):

    W = m_dot Z1 R T1 / M x k/(k-1) x [(P2/P1)^((k-1)/k) - 1] / eta
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

from pydantic import Field

from pipeline_analytics.core.units import magnitude_in
from pipeline_analytics.hydraulics.fluids import R_UNIVERSAL, Gas, pipe_area, to_abs
from pipeline_analytics.hydraulics.friction import darcy_friction, reynolds
from pipeline_analytics.hydraulics.profile import (
    Offtake,
    Profile,
    per_point,
    segment_fractions,
    with_points,
)
from pipeline_analytics.hydraulics.stations import (
    MarchResult,
    Station,
    envelope_checks,
    march,
    plan_stations,
)
from pipeline_analytics.mechanical.common import CheckResult, G, _Frozen

DEFAULT_ROUGHNESS_M = 45.7e-6  # commercial steel; internally coated pipe ~5-10 um
API_14E_C_SI = 122.0  # erosional velocity V_e = C / sqrt(rho), C = 100 in US units


class GasLine:
    """Friction and pressure step for one pipe/gas/flow combination."""

    def __init__(
        self,
        profile: Profile,
        id_m: float,
        std_flow_m3_s: float,
        gas: Gas,
        roughness_m: float = DEFAULT_ROUGHNESS_M,
        efficiency: float = 1.0,
        z_override: float | None = None,
        flow_fraction: Sequence[float] | None = None,
    ) -> None:
        if not 0 < efficiency <= 1:
            raise ValueError("pipeline efficiency must be in (0, 1]")
        self.profile = profile
        self.id_m = id_m
        self.gas = gas
        self.area = pipe_area(id_m)
        self.mass_flow = std_flow_m3_s * gas.std_density
        """Inlet mass flow (kg/s)."""
        nseg = len(profile.chainage_m) - 1
        frac = tuple(flow_fraction) if flow_fraction is not None else (1.0,) * nseg
        if len(frac) != nseg:
            raise ValueError("flow_fraction needs one value per profile segment")
        self.seg_mass = tuple(self.mass_flow * f for f in frac)
        self.seg_friction = tuple(
            darcy_friction(reynolds(m, id_m, gas.viscosity_pa_s), roughness_m / id_m)
            / efficiency**2
            for m in self.seg_mass
        )
        self.re = reynolds(self.mass_flow, id_m, gas.viscosity_pa_s)
        self.friction = self.seg_friction[0]
        self.z_override = z_override

    def z(self, p_abs: float) -> float:
        return self.z_override if self.z_override is not None else self.gas.z(p_abs)

    def step(self, p1: float, k: int) -> float:
        prof = self.profile
        length = prof.chainage_m[k + 1] - prof.chainage_m[k]
        dh = prof.elevation_m[k + 1] - prof.elevation_m[k]
        t, m = self.gas.flowing_temperature_k, self.gas.molar_mass
        p2 = p1
        for _ in range(4):
            p_avg = 2 / 3 * (p1 + p2 - p1 * p2 / (p1 + p2)) if p2 > 0 else p1
            z = self.z(p_avg)
            s = 2 * G * m * dh / (z * R_UNIVERSAL * t)
            le = length * math.expm1(s) / s if abs(s) > 1e-12 else length
            rhs = (
                self.seg_friction[k]
                * self.seg_mass[k] ** 2
                * z
                * R_UNIVERSAL
                * t
                * le
                / (self.id_m * self.area**2 * m)
            )
            val = p1**2 - rhs
            if val <= 0:
                return math.nan
            p2 = math.sqrt(val / math.exp(s))
        return p2

    def velocity(self, p_abs: float, k: int = 0) -> float:
        """Velocity at point k, using the flow of the segment leaving it."""
        m = self.seg_mass[min(k, len(self.seg_mass) - 1)]
        return m / (self.gas.density(p_abs) * self.area)


def compressor_power_w(
    mass_flow_kg_s: float,
    suction_abs_pa: float,
    discharge_abs_pa: float,
    gas: Gas,
    efficiency: float = 0.80,
) -> float:
    k = gas.heat_capacity_ratio
    t1 = gas.flowing_temperature_k
    z1 = gas.z(suction_abs_pa, t1)
    head = z1 * R_UNIVERSAL * t1 / gas.molar_mass * k / (k - 1)
    head *= (discharge_abs_pa / suction_abs_pa) ** ((k - 1) / k) - 1
    return float(mass_flow_kg_s * head / efficiency)


class GasStationInput(_Frozen):
    supply_pressure_pa_g: float = Field(description="pressure available at the inlet")
    delivery_min_pa_g: float
    min_suction_pa_g: float = 40e5
    max_discharge_pa_g: float = Field(default=math.inf, gt=0)
    max_ratio: float = Field(default=1.6, gt=1.0)
    compressor_efficiency: float = Field(default=0.80, gt=0, le=1)
    driver_efficiency: float = Field(default=0.33, gt=0, le=1, description="gas turbine")
    max_velocity_m_s: float = Field(default=20.0, gt=0)


class GasHydraulicsResult(_Frozen):
    march: MarchResult
    mass_flow_kg_s: float
    reynolds: float
    friction_factor: float
    velocity_m_s: tuple[float, ...]
    fuel_gas_kg_s: float
    checks: tuple[CheckResult, ...]

    @property
    def stations(self) -> tuple[Station, ...]:
        return self.march.stations

    @property
    def total_power_w(self) -> float:
        return self.march.total_power_w

    def fuel_gas_std_m3_per_year(self, gas: Gas, load_factor: float = 1.0) -> float:
        return self.fuel_gas_kg_s * 3600 * 8760 * load_factor / gas.std_density


def gas_pressure_profile(
    profile: Profile,
    id_m: Any,
    std_flow: Any,
    inlet_pressure_pa_g: Any,
    gas: Gas | None = None,
    **line_kw: Any,
) -> tuple[float, ...]:
    """Gauge pressure at each profile point for a fixed inlet pressure (no stations).

    ``std_flow`` in standard m^3/s if bare, or a Quantity such as ``Q_(6, "MMSCMD")``.
    NaN marks points the flow cannot reach.
    """
    line = GasLine(
        profile,
        magnitude_in(id_m, "m"),
        magnitude_in(std_flow, "m**3/s"),
        gas or Gas(),
        **line_kw,
    )
    return tuple(march(profile, line.step, magnitude_in(inlet_pressure_pa_g, "Pa")))


def gas_hydraulics(
    profile: Profile,
    id_m: Any,
    std_flow: Any,
    stations: GasStationInput,
    maop_pa_g: float | tuple[float, ...],
    gas: Gas | None = None,
    offtakes: Sequence[Offtake] = (),
    **line_kw: Any,
) -> GasHydraulicsResult:
    """Site compressor stations and compute pressures, velocities, power and fuel.

    ``offtakes`` take a share of the inlet flow part-way along the line and set a
    minimum pressure there. They are inserted into the profile if needed (a
    per-point ``maop_pa_g`` must then already include those points).
    """
    gas = gas or Gas()
    profile = with_points(profile, [o.chainage_m for o in offtakes])
    line = GasLine(
        profile,
        magnitude_in(id_m, "m"),
        magnitude_in(std_flow, "m**3/s"),
        gas,
        flow_fraction=segment_fractions(profile, offtakes),
        **line_kw,
    )
    n = len(profile.chainage_m)
    maop = per_point(maop_pa_g, n, "maop_pa_g")
    min_line = list(per_point(0.0, n, "min_line"))
    for o in offtakes:
        min_line[profile.chainage_m.index(o.chainage_m)] = o.min_pressure_pa_g

    def power(i: int, s_abs: float, d_abs: float) -> float:
        m = line.seg_mass[min(i, n - 2)]
        return compressor_power_w(m, s_abs, d_abs, gas, stations.compressor_efficiency)

    result = plan_stations(
        profile,
        line.step,
        supply_pa_g=stations.supply_pressure_pa_g,
        delivery_min_pa_g=stations.delivery_min_pa_g,
        maop_pa_g=maop,
        min_line_pa_g=min_line,
        min_suction_pa_g=stations.min_suction_pa_g,
        discharge_cap_pa_g=stations.max_discharge_pa_g,
        power=power,
        max_ratio=stations.max_ratio,
    )
    vel = tuple(line.velocity(to_abs(p), k) for k, p in enumerate(result.pressure_pa_g))
    rho = [gas.density(to_abs(p)) for p in result.pressure_pa_g]
    erosional = min(API_14E_C_SI / math.sqrt(r) for r in rho)
    fuel = result.total_power_w / (stations.driver_efficiency * gas.lower_heating_value_j_kg)
    checks = (
        *envelope_checks(result, maop, min_line, stations.delivery_min_pa_g, "gas hydraulics"),
        CheckResult(
            name="gas velocity",
            demand=max(vel),
            capacity=min(stations.max_velocity_m_s, erosional),
            unit="m/s",
            ref="design velocity limit / API RP 14E erosional velocity",
        ),
        *(
            CheckResult(
                name=f"compression ratio at {s.chainage_m / 1000:.1f} km",
                demand=s.ratio,
                capacity=stations.max_ratio,
                unit="-",
                ref="station design limit",
            )
            for s in result.stations
        ),
    )
    return GasHydraulicsResult(
        march=result,
        mass_flow_kg_s=line.mass_flow,
        reynolds=line.re,
        friction_factor=line.friction,
        velocity_m_s=vel,
        fuel_gas_kg_s=fuel,
        checks=checks,
    )
