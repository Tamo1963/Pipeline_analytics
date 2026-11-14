"""Pipe checks for liquefaction: bank lateral spread (HDD setback) and flotation.

* :func:`bank_spread_check` runs the pipe-soil FE model where a pipe leaves a
  river crossing through a spreading bank, for a range of HDD exit setbacks
  from the toe of the bank. It reports the strain utilisation at each, and the
  smallest setback that passes. Extending the HDD beyond the spreading ground
  is the usual remedy.
* :func:`flotation_check` treats liquefied soil as a heavy fluid (ALA 2001) and
  gives the net uplift on an empty pipe and the concrete coating that would
  hold it down. Anchors, or keeping the pipe below the liquefiable layer, are
  the usual alternatives.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict

from pipeline_analytics.geotech.lateral_spread import LateralSpread
from pipeline_analytics.mechanical.buoyancy import BuoyancyInput, buoyancy
from pipeline_analytics.mechanical.common import PipeSection
from pipeline_analytics.mechanical.pgd import analyse_pgd, lateral_spread_movement
from pipeline_analytics.mechanical.seismic import PerformanceLevel
from pipeline_analytics.mechanical.soil import SoilSprings

DEFAULT_SETBACKS_M = (0.0, 25.0, 50.0, 100.0, 150.0, 200.0, 300.0, 400.0, 500.0)


class _M(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", ser_json_inf_nan="constants")


class SetbackCase(_M):
    setback_m: float
    displacement_m: float
    max_tensile_strain: float
    max_compressive_strain: float
    utilisation: float
    passed: bool
    notes: tuple[str, ...]


class BankSpreadCheck(_M):
    cases: tuple[SetbackCase, ...]
    required_setback_m: float | None
    """Smallest setback tested that passes (None if none does)."""
    skew_deg: float
    transition_m: float
    spring_factor: float


def bank_spread_check(
    pipe: PipeSection,
    springs: SoilSprings,
    spread: LateralSpread,
    *,
    grade: str,
    pressure_pa: float,
    setbacks_m: Sequence[float] = DEFAULT_SETBACKS_M,
    skew_deg: float = 0.0,
    transition_m: float = 0.0,
    moving_ground_spring_factor: float = 1.0,
    level: PerformanceLevel = PerformanceLevel.NORMAL_OPERABILITY,
) -> BankSpreadCheck:
    cases = []
    for sb in setbacks_m:
        d = spread.displacement_at(sb)
        if d <= 0:
            cases.append(SetbackCase(setback_m=sb, displacement_m=0.0, max_tensile_strain=0.0,
                                     max_compressive_strain=0.0, utilisation=0.0, passed=True,
                                     notes=("beyond the spreading ground",)))  # fmt: skip
            continue
        mv = lateral_spread_movement(spread, skew_deg, hdd_setback_m=sb, transition_m=transition_m)
        a = analyse_pgd(pipe, springs, mv, grade=grade, pressure=pressure_pa, level=level,
                        moving_ground_spring_factor=moving_ground_spring_factor)  # fmt: skip
        cases.append(SetbackCase(
            setback_m=sb, displacement_m=d, max_tensile_strain=a.fe.max_tensile_strain,
            max_compressive_strain=a.fe.max_compressive_strain,
            utilisation=a.utilisation if a.fe.converged else float("inf"), passed=a.passed,
            notes=a.notes,
        ))  # fmt: skip
    ok = [c.setback_m for c in cases if c.passed]
    return BankSpreadCheck(
        cases=tuple(cases), required_setback_m=min(ok) if ok else None, skew_deg=skew_deg,
        transition_m=transition_m, spring_factor=moving_ground_spring_factor,
    )  # fmt: skip


class FlotationCheck(_M):
    liquefied_density_kg_m3: float
    net_uplift_n_per_m: float
    floats: bool
    required_concrete_mm: float
    utilisation: float


def flotation_check(
    pipe: PipeSection, liquefied_density_kg_m3: float = 1850.0, **kw: Any
) -> FlotationCheck:
    """Empty-pipe flotation in liquefied soil (ALA 2001: liquefied soil as a heavy fluid)."""
    b = buoyancy(pipe, BuoyancyInput(fluid_density_kg_m3=liquefied_density_kg_m3, **kw))
    return FlotationCheck(
        liquefied_density_kg_m3=liquefied_density_kg_m3,
        net_uplift_n_per_m=b.net_upward_n_per_m,
        floats=not b.check.passed,
        required_concrete_mm=b.required_concrete_thickness_m * 1000,
        utilisation=b.check.utilisation,
    )
