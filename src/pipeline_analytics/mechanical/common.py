"""Shared types for mechanical design calculations.

Conventions
-----------
* Public functions accept pint Quantities or bare numbers. Bare numbers are
  read in the SI unit named in the parameter (``od_m``, ``pressure_pa`` ...).
* Results are frozen Pydantic models with SI floats whose field names carry the
  unit (``force_n_per_m``), so they serialise cleanly to JSON for the bot and UI.
* Every design check returns a :class:`CheckResult` with the clause it comes
  from. Values marked ``verify`` in docs/mechanical-ala.md must be confirmed
  against the licensed documents before a design is issued.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from itertools import pairwise
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pipeline_analytics.core.units import magnitude_in

# ALA 2001 reference used throughout.
ALA = "ALA 2001 Guidelines for the Design of Buried Steel Pipe"

G = 9.80665  # m/s^2
WATER_DENSITY = 1000.0  # kg/m^3 (fresh water)


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SteelProperties(_Frozen):
    """Elastic and thermal properties of carbon steel line pipe."""

    youngs_modulus_pa: float = Field(default=207e9, gt=0)
    poisson: float = Field(default=0.3, gt=0, lt=0.5)
    thermal_expansion_per_c: float = Field(default=11.7e-6, gt=0)
    density_kg_m3: float = Field(default=7850.0, gt=0)


CARBON_STEEL = SteelProperties()


class PipeSection(_Frozen):
    """Cross-section of a steel pipe (bare steel; coatings are handled separately)."""

    od_m: float = Field(gt=0)
    wt_m: float = Field(gt=0)
    smys_pa: float = Field(gt=0)
    steel: SteelProperties = CARBON_STEEL

    @classmethod
    def of(cls, od: Any, wt: Any, smys: Any, steel: SteelProperties = CARBON_STEEL) -> PipeSection:
        """Build from Quantities or bare SI numbers (m, m, Pa)."""
        return cls(
            od_m=magnitude_in(od, "m"),
            wt_m=magnitude_in(wt, "m"),
            smys_pa=magnitude_in(smys, "Pa"),
            steel=steel,
        )

    @model_validator(mode="after")
    def _check_wall(self) -> PipeSection:
        if self.wt_m >= self.od_m / 2:
            raise ValueError(f"wall {self.wt_m} m is not valid for OD {self.od_m} m")
        return self

    @property
    def id_m(self) -> float:
        return self.od_m - 2 * self.wt_m

    @property
    def mean_radius_m(self) -> float:
        return (self.od_m - self.wt_m) / 2

    @property
    def steel_area_m2(self) -> float:
        return math.pi / 4 * (self.od_m**2 - self.id_m**2)

    @property
    def second_moment_m4(self) -> float:
        """Second moment of area of the pipe cross-section, I."""
        return math.pi / 64 * (self.od_m**4 - self.id_m**4)

    @property
    def wall_second_moment_m3(self) -> float:
        """Second moment of area of the wall per unit length, t^3/12 (ring stiffness)."""
        return self.wt_m**3 / 12

    @property
    def d_over_t(self) -> float:
        return self.od_m / self.wt_m

    @property
    def weight_n_per_m(self) -> float:
        return self.steel_area_m2 * self.steel.density_kg_m3 * G


def interp(x: float, xs: Sequence[float], ys: Sequence[float]) -> float:
    """Linear interpolation in ascending ``xs``; clamps to the end values."""
    if x <= xs[0]:
        return ys[0]
    for (x0, y0), (x1, y1) in pairwise(zip(xs, ys, strict=True)):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return ys[-1]


class CheckResult(_Frozen):
    """One demand/capacity check."""

    name: str
    demand: float
    capacity: float
    unit: str
    ref: str

    @property
    def utilisation(self) -> float:
        return abs(self.demand) / self.capacity if self.capacity else math.inf

    @property
    def passed(self) -> bool:
        # 1e-9 absorbs round-off when a design is trimmed to exactly meet a limit
        return self.utilisation <= 1.0 + 1e-9

    def __str__(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        return (
            f"{status} {self.name}: {self.demand:.4g} / {self.capacity:.4g} {self.unit} "
            f"(UR {self.utilisation:.2f}) [{self.ref}]"
        )
