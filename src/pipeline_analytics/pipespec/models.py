"""Data models for the PipeSpec Library.

All strengths are in MPa and dimensions in mm (with inch values kept where the
source standard is inch-based). Records carry a ``ref`` to the table they come
from and a ``verified`` flag that is set only after checking against a licensed
copy of the standard.
"""

from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from fractions import Fraction

from pydantic import BaseModel, ConfigDict, Field, model_validator

STEEL_DENSITY_KG_M3 = 7850.0
_WEIGHT_FACTOR = math.pi * STEEL_DENSITY_KG_M3 * 1e-6  # kg/m per mm^2 -> 0.0246615


def plain_end_weight_kg_m(od_mm: float, wt_mm: float) -> float:
    """Plain-end steel pipe mass per metre: ``0.0246615 (D - t) t`` (D, t in mm)."""
    if not 0 < wt_mm < od_mm / 2:
        raise ValueError(f"wall thickness {wt_mm} mm invalid for OD {od_mm} mm")
    return _WEIGHT_FACTOR * (od_mm - wt_mm) * wt_mm


def nps_value(nps: str) -> float:
    """Numeric value of an NPS string: ``"1-1/4"`` -> 1.25, ``"24"`` -> 24.0."""
    whole, _, frac = nps.partition("-")
    if "/" in whole and not frac:
        return float(Fraction(whole))
    return float(int(whole) + (Fraction(frac) if frac else 0))


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Standard(_Frozen):
    id: str
    body: str
    number: str
    title: str
    edition: str
    scope: str


class DeliveryCondition(StrEnum):
    AS_ROLLED = "R"
    NORMALIZED = "N"
    QUENCHED_TEMPERED = "Q"
    THERMOMECHANICAL = "M"


class Grade(_Frozen):
    """A steel grade. For API 5L, one record per (PSL, grade)."""

    standard: str
    name: str
    iso_name: str | None = None
    psl: int | None = Field(default=None, ge=1, le=2)
    conditions: tuple[DeliveryCondition, ...] = ()
    smys: float = Field(gt=0, description="Specified minimum yield strength, MPa")
    smys_max: float | None = None
    smts: float = Field(gt=0, description="Specified minimum tensile strength, MPa")
    smts_max: float | None = None
    yt_max: float | None = Field(default=None, gt=0, le=1)
    ref: str
    verified: bool = False

    @model_validator(mode="after")
    def _check(self) -> Grade:
        if self.smts < self.smys:
            raise ValueError(f"{self.label}: SMTS < SMYS")
        if self.smys_max is not None and self.smys_max <= self.smys:
            raise ValueError(f"{self.label}: SMYS max <= SMYS min")
        if self.smts_max is not None and self.smts_max <= self.smts:
            raise ValueError(f"{self.label}: SMTS max <= SMTS min")
        if self.psl == 2 and not self.conditions:
            raise ValueError(f"{self.label}: PSL2 grade needs delivery conditions")
        return self

    @property
    def label(self) -> str:
        psl = f" PSL{self.psl}" if self.psl else ""
        iso = f" ({self.iso_name})" if self.iso_name else ""
        return f"{self.standard} {self.name}{iso}{psl}"

    def designations(self) -> list[str]:
        """Full designations, e.g. ``["L485Q/X70Q", "L485M/X70M"]`` for PSL2 X70."""
        if not self.conditions:
            return [f"{self.iso_name}/{self.name}" if self.iso_name else self.name]
        return [f"{self.iso_name}{c.value}/{self.name}{c.value}" for c in self.conditions]


class WallThickness(_Frozen):
    wt_in: float = Field(gt=0)
    designations: tuple[str, ...]

    @property
    def wt_mm(self) -> float:
        """Wall thickness in mm, rounded half-up to 0.01 mm as in B36.10M (0.375 in -> 9.53)."""
        mm = Decimal(str(self.wt_in)) * Decimal("25.4")
        return float(mm.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    @property
    def schedule(self) -> str | None:
        """Numeric schedule (e.g. ``"40"``), if any."""
        return next((d for d in self.designations if d.isdigit()), None)


class PipeSize(_Frozen):
    standard: str
    nps: str
    dn: int
    od_in: float
    od_mm: float
    wall_thicknesses: tuple[WallThickness, ...]

    @property
    def nps_value(self) -> float:
        return nps_value(self.nps)

    def wt(self, designation: str) -> WallThickness:
        """Wall thickness for a schedule or STD/XS/XXS designation."""
        key = designation.upper().removeprefix("SCH").strip()
        for wt in self.wall_thicknesses:
            if key in wt.designations:
                return wt
        raise KeyError(f"NPS {self.nps}: no wall thickness designated {designation!r}")

    def weight_kg_m(self, wt_mm: float) -> float:
        return plain_end_weight_kg_m(self.od_mm, wt_mm)


class ManufacturingProcess(_Frozen):
    code: str
    name: str
    psl: tuple[int, ...]
    od_mm: tuple[float, float]
    wt_mm: tuple[float, float]
    joint_factor: float = Field(gt=0, le=1)

    def can_make(self, od_mm: float, wt_mm: float | None = None) -> bool:
        ok = self.od_mm[0] <= od_mm <= self.od_mm[1]
        if wt_mm is not None:
            ok = ok and self.wt_mm[0] <= wt_mm <= self.wt_mm[1]
        return ok


class Coating(_Frozen):
    code: str
    name: str
    standard: str
    part: str
    function: str
    temp_min_c: float | None
    temp_max_c: float | None


class PipeOption(_Frozen):
    """A candidate pipe: size + wall thickness + grade + process."""

    size: PipeSize
    wt_mm: float
    grade: Grade
    process: ManufacturingProcess

    @property
    def weight_kg_m(self) -> float:
        return self.size.weight_kg_m(self.wt_mm)

    @property
    def d_over_t(self) -> float:
        return self.size.od_mm / self.wt_mm
