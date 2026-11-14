"""Buoyancy control (ALA 2001 buoyancy section).

A pipe in water, swamp or liquefiable soil floats if the weight of fluid it
displaces exceeds its own weight. The check uses the empty pipe (worst case for
gas lines, and for liquid lines during construction and pigging):

    W_steel + W_coating + W_ballast >= SF x B,   B = rho_f g pi D_o^2 / 4

``rho_f`` is the density of the surrounding fluid: about 1000 kg/m^3 for fresh
water, 1025 for sea water, and 1800 to 2100 for liquefied soil (ALA treats
liquefied soil as a heavy fluid). The required concrete weight coating
thickness is solved directly. Backfill uplift resistance (soil.uplift_spring)
may be counted when the soil is not liquefied and will stay in place.
"""

from __future__ import annotations

import math
from typing import Any

from pydantic import Field

from pipeline_analytics.core.units import magnitude_in
from pipeline_analytics.mechanical.common import ALA, CheckResult, G, PipeSection, _Frozen


class BuoyancyInput(_Frozen):
    fluid_density_kg_m3: float = Field(default=1000.0, gt=0)
    safety_factor: float = Field(default=1.1, ge=1.0)
    corrosion_coating_thickness_m: float = Field(default=0.003, ge=0)
    corrosion_coating_density_kg_m3: float = Field(default=940.0, gt=0)
    contents_density_kg_m3: float = Field(default=0.0, ge=0, description="0 = empty pipe")
    concrete_thickness_m: float = Field(default=0.0, ge=0)
    concrete_density_kg_m3: float = Field(default=2400.0, gt=0)
    other_resistance_n_per_m: float = Field(
        default=0.0, ge=0, description="e.g. soil uplift or set-on weights per metre"
    )


class BuoyancyResult(_Frozen):
    buoyancy_n_per_m: float
    resisting_weight_n_per_m: float
    net_upward_n_per_m: float
    required_concrete_thickness_m: float
    check: CheckResult


def _annulus(d_in: float, d_out: float) -> float:
    return math.pi / 4 * (d_out**2 - d_in**2)


def buoyancy(pipe: PipeSection, b: BuoyancyInput) -> BuoyancyResult:
    """Buoyancy check and the concrete weight coating needed to pass it."""
    d_c = pipe.od_m + 2 * b.corrosion_coating_thickness_m
    base_weight = (
        pipe.weight_n_per_m
        + _annulus(pipe.od_m, d_c) * b.corrosion_coating_density_kg_m3 * G
        + math.pi / 4 * pipe.id_m**2 * b.contents_density_kg_m3 * G
        + b.other_resistance_n_per_m
    )

    def weights(t_cwc: float) -> tuple[float, float]:
        d_o = d_c + 2 * t_cwc
        uplift = b.fluid_density_kg_m3 * G * math.pi / 4 * d_o**2
        resist = base_weight + _annulus(d_c, d_o) * b.concrete_density_kg_m3 * G
        return uplift, resist

    uplift, resist = weights(b.concrete_thickness_m)
    return BuoyancyResult(
        buoyancy_n_per_m=uplift,
        resisting_weight_n_per_m=resist,
        net_upward_n_per_m=uplift - resist,
        required_concrete_thickness_m=required_concrete_thickness(d_c, base_weight, b),
        check=CheckResult(
            name="negative buoyancy",
            demand=b.safety_factor * uplift,
            capacity=resist,
            unit="N/m",
            ref=f"{ALA} (buoyancy)",
        ),
    )


def required_concrete_thickness(d_coated: Any, base_weight: Any, b: BuoyancyInput) -> float:
    """Smallest concrete coating thickness t with resist(t) >= SF x uplift(t).

    With x = (D_c + 2t)^2 the condition is linear in x:
    ``W0 + rho_c g pi/4 (x - D_c^2) >= SF rho_f g pi/4 x``.
    Returns ``inf`` if concrete is not dense enough (rho_c <= SF rho_f).
    """
    d_c = magnitude_in(d_coated, "m")
    w0 = magnitude_in(base_weight, "N/m")
    k = G * math.pi / 4
    rho_c, rho_f = b.concrete_density_kg_m3, b.safety_factor * b.fluid_density_kg_m3
    if w0 >= rho_f * k * d_c**2:
        return 0.0
    if rho_c <= rho_f:
        return math.inf
    x = (rho_c * k * d_c**2 - w0) / ((rho_c - rho_f) * k)
    return (math.sqrt(x) - d_c) / 2
