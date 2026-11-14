"""Soil properties and pipe-soil interaction springs (ALA 2001 Appendix B).

The four bilinear springs (axial, lateral, vertical uplift, vertical bearing)
give the maximum soil force per unit length of pipe and the relative
displacement at which it develops. They feed thermal anchor-length,
buoyancy/uplift, seismic and permanent ground deformation (PGD) checks, and
later the finite-element models for fault crossings and landslides.

Symbols (ALA notation): D pipe OD, H depth to pipe centreline, c cohesion,
phi internal friction angle, gamma total unit weight, gamma_bar effective unit
weight (submerged below the water table), K0 at-rest earth pressure coefficient,
f coating friction factor, alpha adhesion factor.
"""

from __future__ import annotations

import math
from enum import StrEnum
from typing import Any

from pydantic import Field, model_validator

from pipeline_analytics.core.units import Q_, magnitude_in
from pipeline_analytics.mechanical.common import ALA, WATER_DENSITY, G, _Frozen, interp


class SoilType(StrEnum):
    """Soil class used to choose spring displacements."""

    DENSE_SAND = "dense_sand"
    LOOSE_SAND = "loose_sand"
    STIFF_CLAY = "stiff_clay"
    SOFT_CLAY = "soft_clay"

    @property
    def granular(self) -> bool:
        return self in (SoilType.DENSE_SAND, SoilType.LOOSE_SAND)


class Coating(StrEnum):
    """External coating, for the axial friction factor f (ALA Table B.1)."""

    CONCRETE = "concrete"
    COAL_TAR = "coal_tar"
    ROUGH_STEEL = "rough_steel"
    SMOOTH_STEEL = "smooth_steel"
    FBE = "fbe"
    POLYETHYLENE = "polyethylene"


COATING_FRICTION_FACTOR = {
    Coating.CONCRETE: 1.0,
    Coating.COAL_TAR: 0.9,
    Coating.ROUGH_STEEL: 0.8,
    Coating.SMOOTH_STEEL: 0.7,
    Coating.FBE: 0.6,
    Coating.POLYETHYLENE: 0.6,
}

# Axial displacement to mobilise peak friction (ALA B.2): 3, 5, 8, 10 mm.
_AXIAL_DISPLACEMENT_M = {
    SoilType.DENSE_SAND: 0.003,
    SoilType.LOOSE_SAND: 0.005,
    SoilType.STIFF_CLAY: 0.008,
    SoilType.SOFT_CLAY: 0.010,
}


class Soil(_Frozen):
    """Backfill / native soil around the pipe."""

    soil_type: SoilType
    unit_weight_n_m3: float = Field(gt=0, description="total unit weight gamma")
    friction_angle_deg: float = Field(default=0.0, ge=0, le=45)
    cohesion_pa: float = Field(default=0.0, ge=0)
    k0: float | None = Field(default=None, gt=0, description="default 1 - sin(phi)")
    below_water_table: bool = False

    @classmethod
    def of(
        cls,
        soil_type: SoilType,
        unit_weight: Any,
        friction_angle_deg: float = 0.0,
        cohesion: Any = 0.0,
        **kw: Any,
    ) -> Soil:
        """Build from Quantities or bare SI numbers (N/m^3, Pa)."""
        return cls(
            soil_type=soil_type,
            unit_weight_n_m3=magnitude_in(unit_weight, "N/m**3"),
            friction_angle_deg=friction_angle_deg,
            cohesion_pa=magnitude_in(cohesion, "Pa"),
            **kw,
        )

    @model_validator(mode="after")
    def _check(self) -> Soil:
        if self.friction_angle_deg == 0 and self.cohesion_pa == 0:
            raise ValueError("soil needs a friction angle or cohesion")
        if self.below_water_table and self.unit_weight_n_m3 <= WATER_DENSITY * G:
            raise ValueError("saturated unit weight must exceed that of water")
        return self

    @property
    def effective_unit_weight_n_m3(self) -> float:
        """gamma_bar: submerged unit weight below the water table."""
        if self.below_water_table:
            return self.unit_weight_n_m3 - WATER_DENSITY * G
        return self.unit_weight_n_m3

    @property
    def k0_value(self) -> float:
        return (
            self.k0 if self.k0 is not None else 1 - math.sin(math.radians(self.friction_angle_deg))
        )


class SoilSpring(_Frozen):
    """Bilinear (elastic-perfectly plastic) soil spring."""

    direction: str
    max_force_n_per_m: float
    yield_displacement_m: float
    ref: str

    @property
    def stiffness_n_per_m2(self) -> float:
        return self.max_force_n_per_m / self.yield_displacement_m


def adhesion_factor(cohesion_pa: float) -> float:
    """ALA adhesion factor alpha; the fit uses cohesion c in ksf."""
    c = Q_(cohesion_pa, "Pa").to("kip/ft**2").magnitude
    return float(0.608 - 0.123 * c - 0.274 / (c**2 + 1) + 0.695 / (c**3 + 1))


def axial_spring(od: Any, depth: Any, soil: Soil, coating: Coating) -> SoilSpring:
    """ALA B.2: T_u = pi D alpha c + pi D H gamma_bar (1 + K0)/2 tan(f phi).

    ``depth`` is H, the depth to the pipe centreline.
    """
    d = magnitude_in(od, "m")
    h = magnitude_in(depth, "m")
    _check_cover(d, h)
    delta = COATING_FRICTION_FACTOR[coating] * math.radians(soil.friction_angle_deg)
    t_u = math.pi * d * adhesion_factor(soil.cohesion_pa) * soil.cohesion_pa + (
        math.pi
        * d
        * h
        * soil.effective_unit_weight_n_m3
        * (1 + soil.k0_value)
        / 2
        * math.tan(delta)
    )
    return SoilSpring(
        direction="axial",
        max_force_n_per_m=t_u,
        yield_displacement_m=_AXIAL_DISPLACEMENT_M[soil.soil_type],
        ref=f"{ALA}, App. B.2",
    )


# Hansen bearing-capacity fit for horizontal pipe movement (ALA Table B-2):
# N_qh = a + b x + c x^2 + d x^3 + e x^4, x = H/D, for phi = 20..45 deg.
_NQH_PHI = (20.0, 25.0, 30.0, 35.0, 40.0, 45.0)
_NQH_COEF = (
    (2.399, 0.439, -0.03, 1.059e-3, -1.754e-5),
    (3.332, 0.839, -0.090, 5.606e-3, -1.319e-4),
    (4.565, 1.234, -0.089, 4.275e-3, -9.159e-5),
    (6.816, 2.019, -0.146, 7.651e-3, -1.683e-4),
    (10.959, 1.783, 0.045, -5.425e-3, -1.153e-4),
    (17.658, 3.309, 0.048, -6.443e-3, -1.299e-4),
)


def n_qh(friction_angle_deg: float, h_over_d: float) -> float:
    """Horizontal bearing factor for sand; linear in phi between table rows, 0 at phi = 0."""
    values = [sum(k * h_over_d**i for i, k in enumerate(coef)) for coef in _NQH_COEF]
    return interp(friction_angle_deg, (0.0, *_NQH_PHI), (0.0, *values))


def n_ch(h_over_d: float) -> float:
    """Horizontal bearing factor for clay (phi = 0), capped at 9."""
    x = h_over_d
    value = 6.752 + 0.065 * x - 11.063 / (x + 1) ** 2 + 7.119 / (x + 1) ** 3
    return min(value, 9.0)


def lateral_spring(od: Any, depth: Any, soil: Soil, max_ratio: float = 0.15) -> SoilSpring:
    """ALA B.3: P_u = N_ch c D + N_qh gamma_bar H D.

    Yield displacement 0.04 (H + D/2), capped at ``max_ratio`` x D (ALA gives
    0.10 D to 0.15 D).
    """
    d = magnitude_in(od, "m")
    h = magnitude_in(depth, "m")
    _check_cover(d, h)
    x = h / d
    p_u = n_ch(x) * soil.cohesion_pa * d if soil.cohesion_pa else 0.0
    p_u += n_qh(soil.friction_angle_deg, x) * soil.effective_unit_weight_n_m3 * h * d
    return SoilSpring(
        direction="lateral",
        max_force_n_per_m=p_u,
        yield_displacement_m=min(0.04 * (h + d / 2), max_ratio * d),
        ref=f"{ALA}, App. B.3",
    )


def bearing_factors(friction_angle_deg: float) -> tuple[float, float, float]:
    """(N_c, N_q, N_gamma) per ALA B.5 (phi in degrees)."""

    def nq(phi: float) -> float:
        return (
            math.exp(math.pi * math.tan(math.radians(phi)))
            * math.tan(math.radians(45 + phi / 2)) ** 2
        )

    phi = friction_angle_deg
    # ALA evaluates N_c at phi + 0.001 deg throughout, so N_c -> pi + 2 for phi = 0.
    n_c = (nq(phi + 0.001) - 1) / math.tan(math.radians(phi + 0.001))
    return n_c, nq(phi), math.exp(0.18 * phi - 2.5)


def uplift_spring(od: Any, depth: Any, soil: Soil) -> SoilSpring:
    """ALA B.4: Q_u = N_cv c D + N_qv gamma_bar H D.

    N_cv = 2 H/D <= 10; N_qv = phi H / (44 D) <= N_q.
    Yield displacement: sand 0.01 H (dense) to 0.02 H (loose) <= 0.1 D;
    clay 0.1 H (stiff) to 0.2 H (soft) <= 0.2 D.
    """
    d = magnitude_in(od, "m")
    h = magnitude_in(depth, "m")
    _check_cover(d, h)
    x = h / d
    n_cv = min(2 * x, 10.0)
    n_qv = min(soil.friction_angle_deg * x / 44, bearing_factors(soil.friction_angle_deg)[1])
    q_u = n_cv * soil.cohesion_pa * d + n_qv * soil.effective_unit_weight_n_m3 * h * d
    disp = {
        SoilType.DENSE_SAND: min(0.01 * h, 0.1 * d),
        SoilType.LOOSE_SAND: min(0.02 * h, 0.1 * d),
        SoilType.STIFF_CLAY: min(0.1 * h, 0.2 * d),
        SoilType.SOFT_CLAY: min(0.2 * h, 0.2 * d),
    }[soil.soil_type]
    return SoilSpring(
        direction="uplift", max_force_n_per_m=q_u, yield_displacement_m=disp, ref=f"{ALA}, App. B.4"
    )


def bearing_spring(od: Any, depth: Any, soil: Soil) -> SoilSpring:
    """ALA B.5: Q_d = N_c c D + N_q gamma_bar H D + N_gamma gamma D^2 / 2.

    Yield displacement 0.1 D (granular) or 0.2 D (cohesive).
    """
    d = magnitude_in(od, "m")
    h = magnitude_in(depth, "m")
    _check_cover(d, h)
    n_c, n_q, n_gamma = bearing_factors(soil.friction_angle_deg)
    q_d = (
        n_c * soil.cohesion_pa * d
        + n_q * soil.effective_unit_weight_n_m3 * h * d
        + n_gamma * soil.unit_weight_n_m3 * d**2 / 2
    )
    return SoilSpring(
        direction="bearing",
        max_force_n_per_m=q_d,
        yield_displacement_m=(0.1 if soil.soil_type.granular else 0.2) * d,
        ref=f"{ALA}, App. B.5",
    )


class SoilSprings(_Frozen):
    axial: SoilSpring
    lateral: SoilSpring
    uplift: SoilSpring
    bearing: SoilSpring


def soil_springs(od: Any, depth: Any, soil: Soil, coating: Coating) -> SoilSprings:
    """All four ALA soil springs for a pipe at centreline depth ``depth``."""
    return SoilSprings(
        axial=axial_spring(od, depth, soil, coating),
        lateral=lateral_spring(od, depth, soil),
        uplift=uplift_spring(od, depth, soil),
        bearing=bearing_spring(od, depth, soil),
    )


def _check_cover(d: float, h: float) -> None:
    if h < d / 2:
        raise ValueError(f"centreline depth {h} m is less than the pipe radius {d / 2} m")
