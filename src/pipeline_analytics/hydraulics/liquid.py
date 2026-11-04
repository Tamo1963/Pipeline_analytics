"""Steady-state liquid pipeline hydraulics (crude oil and products).

Per segment of length L and rise dz (Darcy-Weisbach):

    P2 = P1 - rho g dz - f (L / D) rho v^2 / 2

Pump power (electrical): ``W = Q dP / (eta_pump eta_motor)``.

The minimum line pressure is the vapour pressure plus a margin, which keeps the
line packed (no slack-line flow) over high points.
"""

from __future__ import annotations

import math
from typing import Any

from pydantic import Field

from pipeline_analytics.core.units import as_quantity, magnitude_in
from pipeline_analytics.hydraulics.fluids import Liquid, pipe_area, to_gauge
from pipeline_analytics.hydraulics.friction import darcy_friction, reynolds
from pipeline_analytics.hydraulics.gas import DEFAULT_ROUGHNESS_M
from pipeline_analytics.hydraulics.profile import Profile, per_point
from pipeline_analytics.hydraulics.stations import (
    MarchResult,
    Station,
    envelope_checks,
    march,
    plan_stations,
)
from pipeline_analytics.mechanical.common import CheckResult, G, _Frozen


def volumetric_flow_m3_s(flow: Any, liquid: Liquid) -> float:
    """Volumetric flow from a volume rate (m^3/s if bare, bpd, ...) or a mass rate (MMTPA)."""
    q = as_quantity(flow, "m**3/s") if not hasattr(flow, "units") else flow
    if q.is_compatible_with("kg/s"):
        return float(q.to("kg/s").magnitude) / liquid.density_kg_m3
    return magnitude_in(q, "m**3/s")


class LiquidLine:
    def __init__(
        self,
        profile: Profile,
        id_m: float,
        flow_m3_s: float,
        liquid: Liquid,
        roughness_m: float = DEFAULT_ROUGHNESS_M,
    ) -> None:
        self.profile = profile
        self.id_m = id_m
        self.liquid = liquid
        self.flow = flow_m3_s
        self.velocity = flow_m3_s / pipe_area(id_m)
        mass = flow_m3_s * liquid.density_kg_m3
        self.re = reynolds(mass, id_m, liquid.dynamic_viscosity_pa_s)
        self.friction = darcy_friction(self.re, roughness_m / id_m)

    @property
    def friction_gradient_pa_m(self) -> float:
        return self.friction / self.id_m * self.liquid.density_kg_m3 * self.velocity**2 / 2

    def step(self, p1: float, k: int) -> float:
        prof = self.profile
        length = prof.chainage_m[k + 1] - prof.chainage_m[k]
        dz = prof.elevation_m[k + 1] - prof.elevation_m[k]
        p2 = p1 - self.liquid.density_kg_m3 * G * dz - self.friction_gradient_pa_m * length
        return p2 if p2 > 0 else math.nan


def pump_power_w(
    flow_m3_s: float, dp_pa: float, pump_eff: float = 0.75, motor_eff: float = 0.95
) -> float:
    return flow_m3_s * dp_pa / (pump_eff * motor_eff)


class PumpStationInput(_Frozen):
    supply_pressure_pa_g: float = 1e5
    delivery_min_pa_g: float = 2e5
    min_suction_pa_g: float = 3e5
    max_discharge_pa_g: float = Field(default=math.inf, gt=0)
    slack_margin_pa: float = Field(default=1e5, ge=0, description="above vapour pressure")
    pump_efficiency: float = Field(default=0.75, gt=0, le=1)
    motor_efficiency: float = Field(default=0.95, gt=0, le=1)
    max_velocity_m_s: float = Field(default=3.0, gt=0)


class LiquidHydraulicsResult(_Frozen):
    march: MarchResult
    flow_m3_s: float
    velocity_m_s: float
    reynolds: float
    friction_factor: float
    checks: tuple[CheckResult, ...]

    @property
    def stations(self) -> tuple[Station, ...]:
        return self.march.stations

    @property
    def total_power_w(self) -> float:
        return self.march.total_power_w


def liquid_pressure_profile(
    profile: Profile,
    id_m: Any,
    flow: Any,
    inlet_pressure_pa_g: Any,
    liquid: Liquid | None = None,
    **line_kw: Any,
) -> tuple[float, ...]:
    """Gauge pressure at each point for a fixed inlet pressure (no stations)."""
    liquid = liquid or Liquid()
    line = LiquidLine(
        profile, magnitude_in(id_m, "m"), volumetric_flow_m3_s(flow, liquid), liquid, **line_kw
    )
    return tuple(march(profile, line.step, magnitude_in(inlet_pressure_pa_g, "Pa")))


def liquid_hydraulics(
    profile: Profile,
    id_m: Any,
    flow: Any,
    stations: PumpStationInput,
    maop_pa_g: float | tuple[float, ...],
    liquid: Liquid | None = None,
    **line_kw: Any,
) -> LiquidHydraulicsResult:
    """Site pump stations and compute the pressure profile and power."""
    liquid = liquid or Liquid()
    line = LiquidLine(
        profile, magnitude_in(id_m, "m"), volumetric_flow_m3_s(flow, liquid), liquid, **line_kw
    )
    n = len(profile.chainage_m)
    maop = per_point(maop_pa_g, n, "maop_pa_g")
    slack = to_gauge(liquid.vapour_pressure_pa_abs) + stations.slack_margin_pa
    min_line = per_point(slack, n, "min_line")

    def power(s_abs: float, d_abs: float) -> float:
        return pump_power_w(
            line.flow, d_abs - s_abs, stations.pump_efficiency, stations.motor_efficiency
        )

    result = plan_stations(
        profile,
        line.step,
        supply_pa_g=stations.supply_pressure_pa_g,
        delivery_min_pa_g=stations.delivery_min_pa_g,
        maop_pa_g=maop,
        min_line_pa_g=min_line,
        min_suction_pa_g=max(stations.min_suction_pa_g, slack),
        discharge_cap_pa_g=stations.max_discharge_pa_g,
        power=power,
    )
    checks = (
        *envelope_checks(result, maop, min_line, stations.delivery_min_pa_g, "liquid hydraulics"),
        CheckResult(
            name="liquid velocity",
            demand=line.velocity,
            capacity=stations.max_velocity_m_s,
            unit="m/s",
            ref="design velocity limit",
        ),
    )
    return LiquidHydraulicsResult(
        march=result,
        flow_m3_s=line.flow,
        velocity_m_s=line.velocity,
        reynolds=line.re,
        friction_factor=line.friction,
        checks=checks,
    )
