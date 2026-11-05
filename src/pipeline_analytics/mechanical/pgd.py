"""Permanent ground deformation (PGD) scenarios on the pipe-soil FE model.

Scenarios (the ground field is applied to the far ends of the soil springs):

* **Fault crossing**: abrupt offset at x = 0, half of it applied to each side.
  The slip is resolved into axial, transverse and vertical components from the
  crossing angle, dip and rake (:func:`fault_movement`).
* **Transverse PGD** (lateral spread or landslide across the pipe): a zone of
  width W moving horizontally, as a rigid block or a cosine profile.
* **Settlement / subsidence**: a vertical trough of width W (cosine) or a
  differential step (a vertical "fault").
* **Longitudinal PGD**: a block of length L moving along the pipe. This
  extends the closed-form screening check in :mod:`seismic`.

Each analysis returns the FE result, ALA strain checks at the requested
performance level, the share of the movement at which a limit is first
reached, and notes on model validity. :func:`crossing_angle_sweep` finds the
fault-crossing angle with the lowest strain utilisation.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any, Literal

import numpy as np
from pydantic import Field

from pipeline_analytics.core.units import magnitude_in
from pipeline_analytics.mechanical.common import CheckResult, PipeSection, _Frozen
from pipeline_analytics.mechanical.fe import (
    FeOptions,
    FeResult,
    GroundField,
    PipeSoilModel,
    SpringSet,
    SteelCurve,
    graded_mesh,
)
from pipeline_analytics.mechanical.seismic import (
    PerformanceLevel,
    StrainLimits,
    compressive_strain_limit,
    strain_checks,
)
from pipeline_analytics.mechanical.soil import SoilSprings


class GroundMovement(_Frozen):
    """Ground displacement field along the pipe (x = 0 at the fault or zone centre)."""

    kind: Literal["fault", "block", "cosine"]
    axial_m: float = 0.0
    transverse_m: float = 0.0
    vertical_m: float = Field(default=0.0, description="positive = up")
    width_m: float = Field(default=0.0, ge=0, description="zone width for block / cosine")
    label: str = ""

    def field(self) -> GroundField:
        comp = np.array([self.axial_m, self.transverse_m, self.vertical_m])[:, None]
        half = self.width_m / 2
        if self.kind == "fault":
            return lambda x: comp * (0.5 * np.sign(x))[None, :]
        if self.kind == "block":
            return lambda x: comp * (np.abs(x) <= half).astype(float)[None, :]
        return lambda x: (
            comp
            * np.where(np.abs(x) <= half, 0.5 * (1 + np.cos(math.pi * x / max(half, 1e-9))), 0.0)[
                None, :
            ]
        )

    @property
    def magnitude_m(self) -> float:
        return math.sqrt(self.axial_m**2 + self.transverse_m**2 + self.vertical_m**2)

    def refine_points(self) -> list[float]:
        if self.kind == "fault":
            return [0.0]
        h = self.width_m / 2
        if self.kind == "block":
            return [-h, h]
        return list(np.linspace(-h, h, 9))

    def fine_half_width(self, od_m: float) -> float:
        base = max(20 * od_m, 10.0)
        if self.kind == "cosine":
            return min(base, self.width_m / 16 + 2 * od_m)
        return base


def fault_movement(
    slip_m: float,
    crossing_angle_deg: float,
    *,
    dip_deg: float = 90.0,
    rake_deg: float = 0.0,
) -> GroundMovement:
    """Fault slip resolved into pipe axes.

    ``crossing_angle_deg`` (beta) is the angle between the pipe axis and the
    fault strike. Rake (Aki-Richards): 0 = strike-slip, +90 = reverse, -90 =
    normal. The block on the +x side is the hanging wall. The sign convention
    puts strike-slip with beta < 90 deg in axial tension (favourable) and
    beta > 90 deg in compression.
    """
    b, dip, rake = map(math.radians, (crossing_angle_deg, dip_deg, rake_deg))
    ds, dd = slip_m * math.cos(rake), slip_m * math.sin(rake)
    dh = dd * math.cos(dip)  # horizontal dip-slip, positive = shortening (reverse)
    return GroundMovement(
        kind="fault",
        axial_m=ds * math.cos(b) - dh * math.sin(b),
        transverse_m=ds * math.sin(b) + dh * math.cos(b),
        vertical_m=dd * math.sin(dip),
        label=f"fault slip {slip_m:g} m, crossing {crossing_angle_deg:g} deg, dip {dip_deg:g}, "
        f"rake {rake_deg:g}",
    )


def transverse_pgd(
    displacement_m: float, width_m: float, pattern: Literal["block", "cosine"] = "cosine"
) -> GroundMovement:
    if width_m <= 0:
        raise ValueError("zone width must be positive")
    return GroundMovement(kind=pattern, transverse_m=displacement_m, width_m=width_m,
                          label=f"transverse PGD {displacement_m:g} m over {width_m:g} m "
                          f"({pattern})")  # fmt: skip


def settlement(
    displacement_m: float, width_m: float = 0.0, pattern: Literal["cosine", "step"] = "cosine"
) -> GroundMovement:
    """Settlement trough (``cosine``, width needed) or differential settlement (``step``)."""
    d = -abs(displacement_m)
    if pattern == "step":
        return GroundMovement(kind="fault", vertical_m=d,
                              label=f"differential settlement {abs(d):g} m")  # fmt: skip
    if width_m <= 0:
        raise ValueError("a settlement trough needs a width")
    return GroundMovement(kind="cosine", vertical_m=d, width_m=width_m,
                          label=f"settlement {abs(d):g} m over {width_m:g} m")  # fmt: skip


def longitudinal_pgd(displacement_m: float, block_length_m: float) -> GroundMovement:
    return GroundMovement(kind="block", axial_m=displacement_m, width_m=block_length_m,
                          label=f"longitudinal PGD {displacement_m:g} m, block "
                          f"{block_length_m:g} m")  # fmt: skip


class PgdAnalysis(_Frozen):
    movement: GroundMovement
    level: PerformanceLevel
    fe: FeResult
    checks: tuple[CheckResult, CheckResult]
    tensile_limit: float
    compressive_limit: float
    limit_fraction: float | None
    """Share of the movement at which a strain limit is first reached (None = never)."""
    steel: SteelCurve
    notes: tuple[str, ...]

    @property
    def utilisation(self) -> float:
        return max(c.utilisation for c in self.checks)

    @property
    def passed(self) -> bool:
        return self.fe.converged and all(c.passed for c in self.checks)

    @property
    def movement_at_limit_m(self) -> float | None:
        return (
            None if self.limit_fraction is None else self.limit_fraction * self.movement.magnitude_m
        )


def _springs(springs: SoilSprings | SpringSet) -> SpringSet:
    return springs if isinstance(springs, SpringSet) else SpringSet.from_ala(springs)


def analyse_pgd(
    pipe: PipeSection,
    springs: SoilSprings | SpringSet,
    movement: GroundMovement,
    *,
    grade: str = "X65",
    steel: SteelCurve | None = None,
    pressure: Any = 0.0,
    level: PerformanceLevel = PerformanceLevel.NORMAL_OPERABILITY,
    limits: StrainLimits | None = None,
    initial_axial_stress: Any = 0.0,
    half_length_m: float | None = None,
    options: FeOptions | None = None,
) -> PgdAnalysis:
    """Run the FE model for one ground movement and check ALA strain limits.

    ``pressure`` (internal, gauge) enters the compressive strain limit;
    ``initial_axial_stress`` (e.g. restrained thermal + Poisson stress,
    compression negative) is applied before the ground moves.
    """
    sp_ = _springs(springs)
    curve = steel or SteelCurve.for_grade(grade, pipe.smys_pa, pipe.steel.youngs_modulus_pa)
    d = pipe.od_m
    reach = movement.width_m / 2 if movement.kind != "fault" else 0.0
    half = half_length_m or max(300.0, 100 * d) + reach
    h_fine = min(max(d / 2, 0.1), 0.5)
    if movement.kind == "cosine":
        h_fine = max(h_fine, movement.width_m / 400)
    x = graded_mesh(-half, half, movement.refine_points(), h_fine, 10.0,
                    movement.fine_half_width(d))  # fmt: skip
    model = PipeSoilModel(
        pipe, sp_, curve, x,
        initial_axial_stress_pa=magnitude_in(initial_axial_stress, "Pa"), options=options,
    )  # fmt: skip
    fe = model.solve(movement.field())
    lim = limits or StrainLimits()
    checks = strain_checks(pipe, fe.max_tensile_strain, fe.max_compressive_strain, pressure,
                           level, lim, "(FE, " + movement.kind + ")")  # fmt: skip
    t_lim = checks[0].capacity
    c_lim = compressive_strain_limit(pipe, pressure, level, limits=lim)
    notes = []
    if not fe.converged:
        notes.append(f"Analysis stopped early: {fe.message}. Strains are for the share applied.")
    if fe.max_rotation_rad > 0.3:
        notes.append(
            f"Pipe rotation {fe.max_rotation_rad:.2f} rad is beyond the moderate-rotation "
            "range of the beam model; strains are well past any limit."
        )
    if fe.end_strain_ratio > 0.8:
        notes.append("Axial strain at the model ends is near yield: increase half_length_m.")
    if fe.max_compressive_strain > c_lim:
        notes.append("Compressive strain exceeds the local-buckling limit; the beam model does "
                     "not represent wrinkling beyond it.")  # fmt: skip
    if "X" in grade and curve.source and "used for" in curve.source:
        notes.append(curve.source)
    return PgdAnalysis(
        movement=movement, level=level, fe=fe, checks=checks,
        tensile_limit=t_lim, compressive_limit=c_lim,
        limit_fraction=fe.fraction_at(t_lim, c_lim), steel=curve, notes=tuple(notes),
    )  # fmt: skip


class AngleCase(_Frozen):
    crossing_angle_deg: float
    max_tensile_strain: float
    max_compressive_strain: float
    utilisation: float
    converged: bool


class AngleSweep(_Frozen):
    cases: list[AngleCase]
    best_angle_deg: float
    slip_m: float


def crossing_angle_sweep(
    pipe: PipeSection,
    springs: SoilSprings | SpringSet,
    slip_m: float,
    angles_deg: Sequence[float] = (30, 45, 60, 75, 90, 105, 120),
    *,
    dip_deg: float = 90.0,
    rake_deg: float = 0.0,
    **kw: Any,
) -> AngleSweep:
    """Strain utilisation against the fault-crossing angle (lowest is best)."""
    cases = []
    for a in angles_deg:
        r = analyse_pgd(pipe, springs, fault_movement(slip_m, a, dip_deg=dip_deg,
                                                      rake_deg=rake_deg), **kw)  # fmt: skip
        cases.append(AngleCase(
            crossing_angle_deg=a,
            max_tensile_strain=r.fe.max_tensile_strain,
            max_compressive_strain=r.fe.max_compressive_strain,
            utilisation=r.utilisation if r.fe.converged else math.inf,
            converged=r.fe.converged,
        ))  # fmt: skip
    best = min(cases, key=lambda c: c.utilisation)
    return AngleSweep(cases=cases, best_angle_deg=best.crossing_angle_deg, slip_m=slip_m)
