"""Earth loads and surface live loads on buried pipe (ALA 2001 vertical-load provisions).

* Earth load: soil prism ``W = gamma H_top D`` (per unit length).
* Surface live load: Boussinesq point load with impact factor.
* Ring deflection: modified Iowa formula
  ``dy = D_l K W r^3 / (E I + 0.061 E' r^3)`` with ``I = t^3/12``.
* Ring buckling: allowable buckling pressure (also used in AWWA M11)
  ``q_a = (1/FS) (32 R_w B' E' E I / D^3)^0.5``.

Crossings of roads and railways (API RP 1102) are a separate module (planned).
"""

from __future__ import annotations

import math
from typing import Any

from pydantic import Field

from pipeline_analytics.core.units import magnitude_in
from pipeline_analytics.mechanical.common import (
    ALA,
    WATER_DENSITY,
    CheckResult,
    G,
    PipeSection,
    _Frozen,
)


def earth_load_n_per_m(od: Any, cover: Any, unit_weight: Any) -> float:
    """Prism earth load ``W = gamma H D`` per unit length; ``cover`` to the pipe crown."""
    d = magnitude_in(od, "m")
    h = magnitude_in(cover, "m")
    gamma = magnitude_in(unit_weight, "N/m**3")
    if h < 0:
        raise ValueError("cover must be >= 0")
    return gamma * h * d


def boussinesq_pressure_pa(
    wheel_load: Any, cover: Any, offset: Any = 0.0, impact_factor: float = 1.5
) -> float:
    """Vertical pressure at the pipe crown from a surface point load.

    ``p = I_f 3 P / (2 pi H^2 (1 + (d/H)^2)^2.5)``, where d is the horizontal
    offset from the load to the pipe axis.
    """
    p = magnitude_in(wheel_load, "N")
    h = magnitude_in(cover, "m")
    d = magnitude_in(offset, "m")
    if h <= 0:
        raise ValueError("cover must be > 0 for a surface load")
    return float(impact_factor * 3 * p / (2 * math.pi * h**2 * (1 + (d / h) ** 2) ** 2.5))


class VerticalLoadInput(_Frozen):
    """Burial condition for deflection and buckling checks."""

    cover_m: float = Field(gt=0, description="soil cover to the pipe crown, H")
    soil_unit_weight_n_m3: float = Field(gt=0)
    modulus_of_soil_reaction_pa: float = Field(gt=0, description="E'")
    water_above_crown_m: float = Field(default=0.0, ge=0, description="h_w")
    deflection_lag: float = Field(default=1.5, ge=1.0, le=2.0, description="D_l")
    bedding_constant: float = Field(default=0.1, gt=0, le=0.11, description="K")
    live_load_pressure_pa: float = Field(default=0.0, ge=0)
    internal_vacuum_pa: float = Field(default=0.0, ge=0)
    deflection_limit: float = Field(default=0.03, gt=0, lt=0.1, description="dy/D limit")
    buckling_safety_factor: float | None = Field(
        default=None, gt=1, description="default 2.5 if H/D >= 2 else 3.0"
    )


class VerticalLoadResult(_Frozen):
    earth_load_n_per_m: float
    live_load_n_per_m: float
    deflection_m: float
    deflection_ratio: float
    buckling_pressure_applied_pa: float
    buckling_pressure_allowable_pa: float
    checks: tuple[CheckResult, ...]


def vertical_loads(pipe: PipeSection, burial: VerticalLoadInput) -> VerticalLoadResult:
    """Ring deflection and buckling of an unpressurised buried pipe.

    Internal pressure re-rounds the pipe and is ignored, which is conservative.
    """
    d = pipe.od_m
    h = burial.cover_m
    e = pipe.steel.youngs_modulus_pa
    e_soil = burial.modulus_of_soil_reaction_pa
    r = pipe.mean_radius_m
    i_wall = pipe.wall_second_moment_m3

    w_earth = earth_load_n_per_m(d, h, burial.soil_unit_weight_n_m3)
    w_live = burial.live_load_pressure_pa * d
    w = w_earth + w_live
    dy = (
        burial.deflection_lag * burial.bedding_constant * w * r**3
        / (e * i_wall + 0.061 * e_soil * r**3)
    )  # fmt: skip

    h_w = min(burial.water_above_crown_m, h)
    r_w = 1 - 0.33 * h_w / h
    b_prime = 1 / (1 + 4 * math.exp(-0.2133 * h))  # 0.065/ft expressed per metre
    fs = burial.buckling_safety_factor or (2.5 if h / d >= 2 else 3.0)
    q_allow = math.sqrt(32 * r_w * b_prime * e_soil * e * i_wall / d**3) / fs
    q_applied = (
        WATER_DENSITY * G * h_w
        + r_w * w_earth / d
        + burial.internal_vacuum_pa
        + burial.live_load_pressure_pa
    )
    return VerticalLoadResult(
        earth_load_n_per_m=w_earth,
        live_load_n_per_m=w_live,
        deflection_m=dy,
        deflection_ratio=dy / d,
        buckling_pressure_applied_pa=q_applied,
        buckling_pressure_allowable_pa=q_allow,
        checks=(
            CheckResult(
                name="ring deflection dy/D",
                demand=dy / d,
                capacity=burial.deflection_limit,
                unit="-",
                ref=f"{ALA} (earth load, Iowa formula)",
            ),
            CheckResult(
                name="ring buckling",
                demand=q_applied,
                capacity=q_allow,
                unit="Pa",
                ref=f"{ALA} (ring buckling)",
            ),
        ),
    )
