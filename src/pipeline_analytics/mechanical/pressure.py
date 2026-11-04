"""Internal pressure design: ALA 2001 defers to ASME B31.4 / B31.8.

Barlow hoop stress ``S_h = P D / (2 t)`` is limited to ``F E T SMYS``:

* Gas (ASME B31.8 sec. 841.1.1; OISD-STD-226 / PNGRB T4S): F by location class,
  temperature derating T per B31.8 Table 841.1.8-1.
* Liquids (ASME B31.4 sec. 403.2.1; OISD-STD-141): F = 0.72, no temperature
  derating up to 121 degC.

India: the Class 1 Division 1 factor (0.80) is not applied by default. Use it only
once confirmed against the current PNGRB T4S / OISD-STD-226 text.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pipeline_analytics.core.jurisdiction import Jurisdiction, Service
from pipeline_analytics.core.units import magnitude_in
from pipeline_analytics.mechanical.common import ALA, CheckResult, PipeSection, _Frozen, interp


class LocationClass(StrEnum):
    CLASS_1_DIV_1 = "1-1"
    CLASS_1_DIV_2 = "1-2"
    CLASS_2 = "2"
    CLASS_3 = "3"
    CLASS_4 = "4"


_B31_8_DESIGN_FACTOR = {
    LocationClass.CLASS_1_DIV_1: 0.80,
    LocationClass.CLASS_1_DIV_2: 0.72,
    LocationClass.CLASS_2: 0.60,
    LocationClass.CLASS_3: 0.50,
    LocationClass.CLASS_4: 0.40,
}
B31_4_DESIGN_FACTOR = 0.72

# ASME B31.8 Table 841.1.8-1: temperature derating factor T for steel pipe.
_DERATING_T_C = (121.0, 149.0, 177.0, 204.0, 232.0)
_DERATING_T = (1.000, 0.967, 0.933, 0.900, 0.867)


def design_factor(
    service: Service,
    location_class: LocationClass = LocationClass.CLASS_1_DIV_2,
    jurisdiction: Jurisdiction = Jurisdiction.INDIA,
) -> float:
    """Hoop-stress design factor F."""
    if service is Service.LIQUID:
        return B31_4_DESIGN_FACTOR
    if jurisdiction is Jurisdiction.INDIA and location_class is LocationClass.CLASS_1_DIV_1:
        location_class = LocationClass.CLASS_1_DIV_2
    return _B31_8_DESIGN_FACTOR[location_class]


def temperature_derating(design_temp_c: float) -> float:
    """B31.8 temperature derating factor T (1.0 up to 121 degC, linear between rows)."""
    if design_temp_c > _DERATING_T_C[-1]:
        raise ValueError(f"design temperature {design_temp_c} degC is above 232 degC")
    return interp(design_temp_c, _DERATING_T_C, _DERATING_T)


class PressureDesign(_Frozen):
    service: Service
    location_class: LocationClass
    design_factor: float
    joint_factor: float
    temperature_factor: float
    pressure_pa: float
    hoop_stress_pa: float
    allowable_hoop_pa: float
    required_wt_m: float
    """Pressure wall thickness plus allowances (corrosion etc.)."""
    maop_pa: float
    """Maximum pressure the given wall (less allowances) can carry."""
    check: CheckResult


def pressure_design(
    pipe: PipeSection,
    pressure: Any,
    service: Service,
    location_class: LocationClass = LocationClass.CLASS_1_DIV_2,
    *,
    joint_factor: float = 1.0,
    design_temp_c: float = 50.0,
    allowance: Any = 0.0,
    jurisdiction: Jurisdiction = Jurisdiction.INDIA,
) -> PressureDesign:
    """Barlow hoop-stress design for internal (gauge) pressure.

    ``pressure`` is design gauge pressure (Pa if bare). ``allowance`` (m if bare)
    is corrosion plus any other thickness allowance; it is excluded from the
    wall that resists pressure.
    """
    p = magnitude_in(pressure, "Pa")
    c = magnitude_in(allowance, "m")
    if p < 0:
        raise ValueError("internal design pressure must be >= 0 (gauge)")
    f = design_factor(service, location_class, jurisdiction)
    t_factor = temperature_derating(design_temp_c) if service is Service.GAS else 1.0
    if service is Service.LIQUID and design_temp_c > 121:
        raise ValueError("B31.4 design above 121 degC needs a specific assessment")
    allowable = f * joint_factor * t_factor * pipe.smys_pa
    t_eff = pipe.wt_m - c
    if t_eff <= 0:
        raise ValueError("allowance consumes the whole wall")
    hoop = p * pipe.od_m / (2 * t_eff)
    ref = "ASME B31.8 841.1.1" if service is Service.GAS else "ASME B31.4 403.2.1"
    return PressureDesign(
        service=service,
        location_class=location_class,
        design_factor=f,
        joint_factor=joint_factor,
        temperature_factor=t_factor,
        pressure_pa=p,
        hoop_stress_pa=hoop,
        allowable_hoop_pa=allowable,
        required_wt_m=p * pipe.od_m / (2 * allowable) + c,
        maop_pa=2 * allowable * t_eff / pipe.od_m,
        check=CheckResult(
            name="hoop stress",
            demand=hoop,
            capacity=allowable,
            unit="Pa",
            ref=f"{ref}; {ALA} (internal pressure)",
        ),
    )
