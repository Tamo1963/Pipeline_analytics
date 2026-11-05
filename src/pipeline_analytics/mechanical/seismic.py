"""Seismic design checks (ALA 2001 seismic sections).

Implemented (closed form):

* **Wave propagation** (transient strain): ground strain ``V / (alpha_e C)`` and
  curvature ``A / (alpha_k C^2)``. Pipe axial strain is capped by soil friction
  over a quarter wavelength: ``T_u lambda / (4 A_s E)``.
* **Longitudinal PGD screening** (lateral spread or landslide block of length L
  moving parallel to the pipe by delta): elastic pipe, friction-limited.
  Strain is the lesser of ``sqrt(2 delta T_u / (A_s E))`` (displacement
  controlled) and ``T_u L / (2 A_s E)`` (block-length controlled).
* **Strain acceptance** for two performance levels (normal operability and
  pressure integrity).

Fault crossings, transverse PGD and settlement use the pipe-soil FE model in
``fe.py`` / ``pgd.py`` (Phase 2c). Note that ``longitudinal_pgd`` below puts the
whole relative movement on one side of the scarp; where strain is
displacement-controlled the FE (friction on both sides) gives 1/sqrt(2) of it,
so this screening value is conservative.
"""

from __future__ import annotations

import math
from enum import StrEnum
from typing import Any

from pydantic import Field

from pipeline_analytics.core.units import magnitude_in
from pipeline_analytics.mechanical.common import ALA, CheckResult, G, PipeSection, _Frozen


class WaveType(StrEnum):
    SHEAR = "S"
    RAYLEIGH = "R"


# (alpha_epsilon, alpha_kappa): ground strain and curvature coefficients.
_WAVE_COEF = {WaveType.SHEAR: (2.0, 1.0), WaveType.RAYLEIGH: (1.0, 1.0)}


class PerformanceLevel(StrEnum):
    NORMAL_OPERABILITY = "normal_operability"
    PRESSURE_INTEGRITY = "pressure_integrity"


class StrainLimits(_Frozen):
    """Longitudinal strain limits. Defaults per ALA 2001; verify before use."""

    tensile_operability: float = Field(default=0.02, gt=0)
    tensile_integrity: float = Field(default=0.04, gt=0)
    integrity_compressive_coeff: float = Field(default=1.76, gt=0, description="x t/D")


def compressive_strain_limit(
    pipe: PipeSection,
    pressure: Any = 0.0,
    level: PerformanceLevel = PerformanceLevel.NORMAL_OPERABILITY,
    *,
    ovality_d_min: Any | None = None,
    limits: StrainLimits | None = None,
) -> float:
    """Compressive (local buckling) strain limit.

    Normal operability: ``0.50 t/D' - 0.0025 + 3000 (p D / (2 E t))^2`` with
    ``D' = D / (1 - 3 (D - D_min) / D)`` for an ovalised pipe; the pressure term
    uses ``p D / (2 E t) <= 0.4 SMYS / E``.
    Pressure integrity: ``1.76 t/D``.
    """
    limits = limits or StrainLimits()
    d, t = pipe.od_m, pipe.wt_m
    if level is PerformanceLevel.PRESSURE_INTEGRITY:
        return limits.integrity_compressive_coeff * t / d
    d_min = magnitude_in(ovality_d_min, "m") if ovality_d_min is not None else d
    d_prime = d / (1 - 3 * (d - d_min) / d)
    p = max(magnitude_in(pressure, "Pa"), 0.0)
    e = pipe.steel.youngs_modulus_pa
    hoop_over_e = min(p * d / (2 * e * t), 0.4 * pipe.smys_pa / e)
    return 0.50 * t / d_prime - 0.0025 + 3000 * hoop_over_e**2


def strain_checks(
    pipe: PipeSection,
    tensile_strain: float,
    compressive_strain: float,
    pressure: Any = 0.0,
    level: PerformanceLevel = PerformanceLevel.NORMAL_OPERABILITY,
    limits: StrainLimits | None = None,
    source: str = "",
) -> tuple[CheckResult, CheckResult]:
    limits = limits or StrainLimits()
    tens_cap = (
        limits.tensile_operability
        if level is PerformanceLevel.NORMAL_OPERABILITY
        else limits.tensile_integrity
    )
    comp_cap = compressive_strain_limit(pipe, pressure, level, limits=limits)
    suffix = f" {source}" if source else ""
    return (
        CheckResult(
            name=f"tensile strain{suffix} ({level})",
            demand=tensile_strain,
            capacity=tens_cap,
            unit="-",
            ref=f"{ALA} (strain criteria)",
        ),
        CheckResult(
            name=f"compressive strain{suffix} ({level})",
            demand=compressive_strain,
            capacity=comp_cap,
            unit="-",
            ref=f"{ALA} (strain criteria)",
        ),
    )


class WavePropagationResult(_Frozen):
    ground_strain: float
    ground_curvature_per_m: float
    pipe_axial_strain: float
    pipe_bending_strain: float
    pipe_total_strain: float
    friction_limited: bool
    checks: tuple[CheckResult, ...]


def wave_propagation(
    pipe: PipeSection,
    pgv: Any,
    pga: Any,
    *,
    apparent_velocity: Any = 2000.0,
    wave_type: WaveType = WaveType.SHEAR,
    axial_soil_resistance: Any | None = None,
    wavelength: Any | None = None,
    pressure: Any = 0.0,
    level: PerformanceLevel = PerformanceLevel.NORMAL_OPERABILITY,
    limits: StrainLimits | None = None,
) -> WavePropagationResult:
    """Transient strain from seismic wave propagation.

    ``pgv`` peak ground velocity (m/s), ``pga`` peak ground acceleration (m/s^2,
    or a Quantity such as ``Q_(0.3, "g0")``), ``apparent_velocity`` C (m/s; 2 km/s
    is a common conservative default for S-waves). If ``axial_soil_resistance``
    T_u and ``wavelength`` are given, axial strain is capped by soil friction.
    """
    v = magnitude_in(pgv, "m/s")
    a = magnitude_in(pga, "m/s**2")
    c = magnitude_in(apparent_velocity, "m/s")
    a_eps, a_kap = _WAVE_COEF[wave_type]
    eps_g = v / (a_eps * c)
    kappa = a / (a_kap * c**2)

    eps_axial, limited = eps_g, False
    if axial_soil_resistance is not None and wavelength is not None:
        t_u = magnitude_in(axial_soil_resistance, "N/m")
        lam = magnitude_in(wavelength, "m")
        cap = t_u * lam / (4 * pipe.steel_area_m2 * pipe.steel.youngs_modulus_pa)
        if cap < eps_g:
            eps_axial, limited = cap, True
    eps_bend = kappa * pipe.od_m / 2
    total = eps_axial + eps_bend
    return WavePropagationResult(
        ground_strain=eps_g,
        ground_curvature_per_m=kappa,
        pipe_axial_strain=eps_axial,
        pipe_bending_strain=eps_bend,
        pipe_total_strain=total,
        friction_limited=limited,
        checks=strain_checks(pipe, total, total, pressure, level, limits, "(wave propagation)"),
    )


class LongitudinalPgdResult(_Frozen):
    strain_displacement_controlled: float
    strain_length_controlled: float
    pipe_strain: float
    governing: str
    elastic_valid: bool
    """False when strain exceeds SMYS/E: use a non-linear (FE) model instead."""
    checks: tuple[CheckResult, ...]


def longitudinal_pgd(
    pipe: PipeSection,
    ground_displacement: Any,
    block_length: Any,
    axial_soil_resistance: Any,
    *,
    pressure: Any = 0.0,
    level: PerformanceLevel = PerformanceLevel.NORMAL_OPERABILITY,
    limits: StrainLimits | None = None,
) -> LongitudinalPgdResult:
    """Screening strain for ground movement parallel to the pipe (elastic pipe).

    The same strain is tensile at the head of the block and compressive at the
    toe, so both limits are checked.
    """
    delta = magnitude_in(ground_displacement, "m")
    length = magnitude_in(block_length, "m")
    t_u = magnitude_in(axial_soil_resistance, "N/m")
    ae = pipe.steel_area_m2 * pipe.steel.youngs_modulus_pa
    eps_d = math.sqrt(2 * delta * t_u / ae)
    eps_l = t_u * length / (2 * ae)
    eps = min(eps_d, eps_l)
    return LongitudinalPgdResult(
        strain_displacement_controlled=eps_d,
        strain_length_controlled=eps_l,
        pipe_strain=eps,
        governing="displacement" if eps_d <= eps_l else "block length",
        elastic_valid=eps <= pipe.smys_pa / pipe.steel.youngs_modulus_pa,
        checks=strain_checks(pipe, eps, eps, pressure, level, limits, "(longitudinal PGD)"),
    )


def g_to_ms2(pga_g: float) -> float:
    """Convenience: PGA in g to m/s^2."""
    return pga_g * G
