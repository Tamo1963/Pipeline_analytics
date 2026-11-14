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

import itertools
import math
from collections.abc import Sequence
from typing import Any, Literal

import numpy as np
from pydantic import Field, model_validator

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

    kind: Literal["fault", "block", "cosine", "table"]
    axial_m: float = 0.0
    transverse_m: float = 0.0
    vertical_m: float = Field(default=0.0, description="positive = up")
    width_m: float = Field(default=0.0, ge=0, description="zone width for block / cosine")
    label: str = ""
    profile_x_m: tuple[float, ...] = ()
    """table: positions along the pipe (x = 0 at the zone reference point)."""
    profile_scale: tuple[float, ...] = ()
    """table: share of the movement at each position (linear between, 0 outside)."""

    @model_validator(mode="after")
    def _check_table(self) -> GroundMovement:
        if self.kind == "table":
            xs = self.profile_x_m
            if len(xs) < 2 or len(xs) != len(self.profile_scale):
                raise ValueError("table movement needs matching profile_x_m / profile_scale")
            if any(b <= a for a, b in itertools.pairwise(xs)):
                raise ValueError("profile_x_m must increase")
        return self

    def field(self) -> GroundField:
        comp = np.array([self.axial_m, self.transverse_m, self.vertical_m])[:, None]
        half = self.width_m / 2
        if self.kind == "fault":
            return lambda x: comp * (0.5 * np.sign(x))[None, :]
        if self.kind == "block":
            return lambda x: comp * (np.abs(x) <= half).astype(float)[None, :]
        if self.kind == "table":
            xs, fr = np.array(self.profile_x_m), np.array(self.profile_scale)
            return lambda x: comp * np.interp(x, xs, fr, left=0.0, right=0.0)[None, :]
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
        if self.kind == "table":
            xs = self.profile_x_m
            return sorted({xs[0], xs[-1], *([0.0] if xs[0] <= 0 <= xs[-1] else [])})
        return list(np.linspace(-h, h, 9))

    def fine_half_width(self, od_m: float) -> float:
        base = max(20 * od_m, 10.0)
        if self.kind == "table":
            return base
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

    ``crossing_angle_deg`` (beta) is the angle from the pipe axis (+x) to the
    fault strike, counter-clockwise, 0-360. Strike follows Aki-Richards (the
    fault dips to the right of the strike), and rake: 0 = left-lateral, 180 =
    right-lateral, +90 = reverse, -90 = normal. For 0 < beta < 180 the block
    ahead of the crossing (+x) is the hanging wall; for 180 < beta < 360 it
    is the footwall. Left-lateral slip with beta < 90 deg stretches the pipe
    (favourable); beta between 90 and 180 deg compresses it.
    """
    b, dip, rake = map(math.radians, (crossing_angle_deg, dip_deg, rake_deg))
    ds, dd = slip_m * math.cos(rake), slip_m * math.sin(rake)
    dh = dd * math.cos(dip)  # horizontal dip-slip, positive = shortening (reverse)
    sx, sy = math.cos(b), math.sin(b)  # strike in pipe axes
    nx, ny = sy, -sx  # right of strike: towards the hanging wall
    # hanging-wall movement relative to the footwall
    mx, my, mz = ds * sx - dh * nx, ds * sy - dh * ny, dd * math.sin(dip)
    sign = 1.0 if nx > 0 or (nx == 0 and ny < 0) else -1.0  # is +x on the hanging wall?
    return GroundMovement(
        kind="fault",
        axial_m=sign * mx,
        transverse_m=sign * my,
        vertical_m=sign * mz,
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


def lateral_spread_movement(
    spread: Any,
    skew_deg: float = 0.0,
    *,
    hdd_setback_m: float = 0.0,
    transition_m: float = 0.0,
    label: str = "",
) -> GroundMovement:
    """Ground movement for a pipe leaving a river crossing through a spreading bank.

    ``spread`` is a ``geotech.LateralSpread`` behind a free face. The pipe runs
    inland along +x, at ``skew_deg`` to the bank normal (0 = square to the bank).
    The ground moves towards the river, so mostly along the pipe, decaying inland
    as the spread profile does (distance from the toe = setback + x cos(skew)).

    * ``hdd_setback_m``: distance from the toe to where the pipe leaves the HDD and
      becomes shallow (x = 0 there). Before it, the pipe is below the liquefiable
      layer and the ground is taken as stable.
    * ``transition_m``: length over which the HDD pipe climbs through the
      liquefiable layer. The movement ramps up over it instead of acting as an
      abrupt step. 0 is conservative.
    """
    if not 0 <= skew_deg < 80:
        raise ValueError("skew must be 0-80 deg; use parallel_spread_movement along a bank")
    d0 = spread.displacement_at(hdd_setback_m)
    if d0 <= 0:
        raise ValueError("no lateral spread at the HDD exit")
    c = math.cos(math.radians(skew_deg))
    xs: list[float] = [-transition_m] if transition_m > 0 else []
    sc: list[float] = [0.0] if transition_m > 0 else []
    xs.append(0.0)
    sc.append(1.0)
    for pt in spread.profile:
        if pt.distance_m > hdd_setback_m:
            xs.append((pt.distance_m - hdd_setback_m) / c)
            sc.append(pt.displacement_m / d0)
    return GroundMovement(
        kind="table",
        axial_m=-d0 * c,
        transverse_m=d0 * math.sin(math.radians(skew_deg)),
        profile_x_m=tuple(xs),
        profile_scale=tuple(sc),
        width_m=xs[-1],
        label=label
        or f"lateral spread {d0:.2f} m at the HDD exit ({hdd_setback_m:g} m from "
        f"the bank), pipe at {skew_deg:g} deg to the bank normal",
    )


def parallel_spread_movement(
    spread: Any, distance_from_toe_m: float, length_along_bank_m: float
) -> GroundMovement:
    """A pipe running along a spreading bank, ``distance_from_toe_m`` inland: the ground
    moves across the pipe over ``length_along_bank_m`` (block with the local D_H)."""
    d = spread.displacement_at(distance_from_toe_m)
    if d <= 0:
        raise ValueError("no lateral spread at that distance from the bank")
    return GroundMovement(
        kind="block", transverse_m=d, width_m=length_along_bank_m,
        label=f"lateral spread {d:.2f} m across the pipe, {distance_from_toe_m:g} m from "
        f"the bank, over {length_along_bank_m:g} m",
    )  # fmt: skip


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
    moving_ground_spring_factor: float = 1.0,
) -> PgdAnalysis:
    """Run the FE model for one ground movement and check ALA strain limits.

    ``pressure`` (internal, gauge) enters the compressive strain limit;
    ``initial_axial_stress`` (e.g. restrained thermal + Poisson stress,
    compression negative) is applied before the ground moves.
    ``moving_ground_spring_factor`` scales all springs where the ground moves,
    e.g. 0.1 for liquefied soil. The default 1.0 keeps full non-liquefied springs,
    which forces the pipe to follow the ground: an upper bound on strain.
    """
    sp_ = _springs(springs)
    curve = steel or SteelCurve.for_grade(grade, pipe.smys_pa, pipe.steel.youngs_modulus_pa)
    d = pipe.od_m
    if movement.kind == "table":
        reach = max(abs(movement.profile_x_m[0]), abs(movement.profile_x_m[-1]))
    else:
        reach = movement.width_m / 2 if movement.kind != "fault" else 0.0
    half = half_length_m or max(300.0, 100 * d) + reach
    h_fine = min(max(d / 2, 0.1), 0.5)
    if movement.kind == "cosine":
        h_fine = max(h_fine, movement.width_m / 400)
    x = graded_mesh(-half, half, movement.refine_points(), h_fine, 10.0,
                    movement.fine_half_width(d))  # fmt: skip
    scale = None
    if moving_ground_spring_factor != 1.0:
        g = movement.field()(x)
        scale = np.where(np.abs(g).max(axis=0) > 1e-9, moving_ground_spring_factor, 1.0)
    model = PipeSoilModel(
        pipe, sp_, curve, x,
        initial_axial_stress_pa=magnitude_in(initial_axial_stress, "Pa"), options=options,
        spring_scale=scale,
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
