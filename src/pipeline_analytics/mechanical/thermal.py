"""Thermal expansion and combined stress (ALA 2001 thermal section; ASME B31.4 / B31.8).

Fully restrained (buried, far from bends) pipe:

    S_L = nu S_h - E alpha (T2 - T1)          (compression negative)

Unrestrained pipe (above ground, or free to slide):

    S_L = S_h / 2

Checks:
* |S_L| <= 0.90 SMYS (B31.4 403.3.1, restrained) or 0.90 S T (B31.8 833.3)
* combined equivalent stress <= 0.90 SMYS (B31.4: Tresca or von Mises;
  B31.8 833.4: max shear or von Mises).

The *virtual anchor length* is the distance from a bend or free end over which
soil friction builds the full restrained axial force:

    L_a = |F_restrained| / T_u,   F_restrained = A_s (nu S_h - E alpha dT)

Bends within L_a of each other see partially restrained growth that must be
checked with a stress model (planned: bends and above-ground transitions).
"""

from __future__ import annotations

import math
from enum import StrEnum
from typing import Any

from pipeline_analytics.core.units import magnitude_in
from pipeline_analytics.mechanical.common import ALA, CheckResult, PipeSection, _Frozen


class Restraint(StrEnum):
    RESTRAINED = "restrained"
    UNRESTRAINED = "unrestrained"


class EquivalentStress(StrEnum):
    VON_MISES = "von_mises"
    TRESCA = "tresca"


class ThermalResult(_Frozen):
    restraint: Restraint
    hoop_stress_pa: float
    longitudinal_stress_pa: float
    equivalent_stress_pa: float
    axial_force_n: float
    """Restrained axial force A_s S_L (negative = compression)."""
    virtual_anchor_length_m: float | None
    checks: tuple[CheckResult, ...]


def thermal_stress(
    pipe: PipeSection,
    pressure: Any,
    temp_install_c: float,
    temp_operate_c: float,
    restraint: Restraint = Restraint.RESTRAINED,
    *,
    axial_soil_resistance: Any | None = None,
    bending_stress: Any = 0.0,
    criterion: EquivalentStress = EquivalentStress.VON_MISES,
    longitudinal_limit: float = 0.90,
    combined_limit: float = 0.90,
    temperature_factor: float = 1.0,
) -> ThermalResult:
    """Longitudinal and combined stress due to pressure and temperature change.

    ``pressure`` is internal gauge pressure (Pa if bare). ``bending_stress``
    (Pa) is added to the longitudinal stress with the worse sign.
    ``axial_soil_resistance`` T_u (N/m), e.g. from
    :func:`pipeline_analytics.mechanical.soil.axial_spring`, gives the virtual
    anchor length.
    """
    p = magnitude_in(pressure, "Pa")
    s_b = abs(magnitude_in(bending_stress, "Pa"))
    st = pipe.steel
    s_h = p * pipe.od_m / (2 * pipe.wt_m)
    d_t = temp_operate_c - temp_install_c

    if restraint is Restraint.RESTRAINED:
        s_l_axial = st.poisson * s_h - st.youngs_modulus_pa * st.thermal_expansion_per_c * d_t
    else:
        s_l_axial = s_h / 2
    # bending adds to whichever side makes |S_L| larger
    s_l = s_l_axial + math.copysign(s_b, s_l_axial)

    if criterion is EquivalentStress.VON_MISES:
        s_eq = math.sqrt(s_h**2 - s_h * s_l + s_l**2)
    else:  # Tresca, radial stress taken as zero
        s_eq = max(abs(s_h - s_l), abs(s_h), abs(s_l))

    force = pipe.steel_area_m2 * s_l_axial if restraint is Restraint.RESTRAINED else 0.0
    t_u = magnitude_in(axial_soil_resistance, "N/m") if axial_soil_resistance is not None else None
    anchor = abs(force) / t_u if (t_u and restraint is Restraint.RESTRAINED) else None

    allow = pipe.smys_pa * temperature_factor
    return ThermalResult(
        restraint=restraint,
        hoop_stress_pa=s_h,
        longitudinal_stress_pa=s_l,
        equivalent_stress_pa=s_eq,
        axial_force_n=force,
        virtual_anchor_length_m=anchor,
        checks=(
            CheckResult(
                name=f"longitudinal stress ({restraint})",
                demand=s_l,
                capacity=longitudinal_limit * allow,
                unit="Pa",
                ref=f"ASME B31.4 403.3 / B31.8 833.3; {ALA} (thermal expansion)",
            ),
            CheckResult(
                name=f"combined stress ({criterion})",
                demand=s_eq,
                capacity=combined_limit * allow,
                unit="Pa",
                ref="ASME B31.4 403.3.4 / B31.8 833.4",
            ),
        ),
    )
