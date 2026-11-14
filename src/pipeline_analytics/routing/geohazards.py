"""Geohazards on the route: mapped faults and ground-movement zones.

Layers (projected metres, like the rest of the AOI):

* :class:`Fault`: a fault trace (polyline) with design slip, dip and rake.
  Digitise the trace so that the fault **dips to the right** of the
  digitising direction (the Aki-Richards strike convention). The block on the
  right is then the hanging wall.
* :class:`GroundZone`: a polygon that moves as a landslide or lateral spread
  (horizontal movement towards an azimuth) or subsides (settlement).

How they enter routing
----------------------
1. **Cost surface.** Faults (buffered by ``zone_half_width_m``) and zones are
   burnt into the ``hazard`` raster (0-3), so the router avoids them through
   the hazard construction factor, as before.
2. **Crossings.** :func:`find_crossings` intersects any alignment with the
   layers. For each fault it gives the crossing point, chainage and crossing
   angle, and resolves the slip into pipe axes. For each zone it gives the
   length inside the zone and the movement in pipe axes.
3. **Strain check.** :func:`assess_geohazards` runs the pipe-soil FE model
   (``mechanical.pgd``) for every crossing with the design's wall and the
   ALA springs. Where a strain limit is exceeded it looks for the lightest
   heavier wall that passes over the affected length and prices it. For a
   failing fault it also reports the best crossing angle.
4. **Realignment.** :func:`realign_fault_crossing` re-routes through two
   waypoints so that the pipe crosses the fault at a chosen angle.

``optimiser.optimise_route`` uses steps 2-3 for every corridor: mitigation
cost is added to lifecycle cost, so a cheaper route with a bad fault crossing
can lose to a dearer one with a safe crossing.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Sequence
from typing import TYPE_CHECKING, Literal

import numpy as np
from numpy.typing import NDArray
from pydantic import Field, model_validator

from pipeline_analytics.cost.rates import UnitRates, _Model
from pipeline_analytics.mechanical.pgd import (
    GroundMovement,
    analyse_pgd,
    crossing_angle_sweep,
    fault_movement,
    settlement,
)
from pipeline_analytics.mechanical.seismic import PerformanceLevel
from pipeline_analytics.mechanical.soil import Coating, Soil, SoilType, soil_springs
from pipeline_analytics.routing.grid import Grid

if TYPE_CHECKING:
    from pipeline_analytics.routing.cost_surface import CostSurface
from pipeline_analytics.routing.rasterize import XY, rasterize_polygons

ZoneKind = Literal["landslide", "lateral_spread", "subsidence"]
PRACTICAL_ANGLES_DEG = (30, 45, 60, 75, 90, 105, 120, 135, 150)
"""Crossing angles considered: flatter crossings put kilometres of pipe in the fault zone."""


class Fault(_Model):
    name: str
    trace: tuple[XY, ...] = Field(min_length=2)
    slip_m: float = Field(gt=0, description="design fault displacement")
    dip_deg: float = Field(default=90.0, gt=0, le=90)
    rake_deg: float = Field(default=0.0, ge=-180, le=180,
                            description="0 left-lateral, 180 right-lateral, 90 reverse, "
                            "-90 normal")  # fmt: skip
    zone_half_width_m: float = Field(default=300.0, ge=0, description="hazard band on the map")
    hazard_class: int = Field(default=3, ge=0, le=3)


class GroundZone(_Model):
    name: str
    polygon: tuple[XY, ...] = Field(min_length=3)
    kind: ZoneKind
    displacement_m: float = Field(gt=0)
    direction_azimuth_deg: float | None = Field(
        default=None, description="movement direction, degrees clockwise from north"
    )
    pattern: Literal["cosine", "block"] = "cosine"
    hazard_class: int = Field(default=2, ge=0, le=3)

    @model_validator(mode="after")
    def _check(self) -> GroundZone:
        if self.kind != "subsidence" and self.direction_azimuth_deg is None:
            raise ValueError(f"{self.kind} zone {self.name!r} needs direction_azimuth_deg")
        return self


class Geohazards(_Model):
    faults: tuple[Fault, ...] = ()
    zones: tuple[GroundZone, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.faults or self.zones)

    def hazard_raster(self, grid: Grid) -> NDArray[np.int_]:
        """Hazard class per cell (max over layers)."""
        out = grid.empty(int)
        xs, ys = grid.centers()
        for f in self.faults:
            if f.zone_half_width_m <= 0 or f.hazard_class == 0:
                continue
            d = np.full(grid.shape, np.inf)
            for (ax, ay), (bx, by) in itertools.pairwise(f.trace):
                d = np.minimum(d, _dist_to_segment(xs, ys, ax, ay, bx, by))
            out = np.maximum(out, np.where(d <= f.zone_half_width_m, f.hazard_class, 0))
        for z in self.zones:
            if z.hazard_class:
                out = np.maximum(out, np.where(rasterize_polygons(grid, [list(z.polygon)]),
                                               z.hazard_class, 0))  # fmt: skip
        return np.asarray(out, dtype=np.int_)


def _dist_to_segment(
    xs: NDArray[np.float64], ys: NDArray[np.float64], ax: float, ay: float, bx: float, by: float
) -> NDArray[np.float64]:
    dx, dy = bx - ax, by - ay
    seg2 = dx * dx + dy * dy
    t = np.clip(((xs - ax) * dx + (ys - ay) * dy) / seg2, 0, 1) if seg2 else 0.0
    return np.asarray(np.hypot(xs - (ax + t * dx), ys - (ay + t * dy)), dtype=np.float64)


def _inside(
    px: NDArray[np.float64], py: NDArray[np.float64], poly: Sequence[XY]
) -> NDArray[np.bool_]:
    """Even-odd point-in-polygon, vectorised over points."""
    inside = np.zeros(px.shape, dtype=bool)
    n = len(poly)
    for i in range(n):
        (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % n]
        cond = (y1 > py) != (y2 > py)
        with np.errstate(divide="ignore", invalid="ignore"):
            xint = x1 + (py - y1) * (x2 - x1) / (y2 - y1)
        inside ^= cond & (px < xint)
    return inside


# ------------------------------------------------------------------ crossings


class HazardCrossing(_Model):
    hazard: str
    kind: Literal["fault", "landslide", "lateral_spread", "subsidence"]
    chainage_m: float
    point: XY
    crossing_angle_deg: float | None = None
    """Faults: angle from the pipe direction to the fault strike, counter-clockwise (0-360)."""
    width_m: float = 0.0
    """Zones: length of pipe inside the zone."""
    movement: GroundMovement
    slip_m: float | None = None
    dip_deg: float = 90.0
    rake_deg: float = 0.0


def _angle_ccw(ux: float, uy: float, vx: float, vy: float) -> float:
    return math.degrees(math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)) % 360


def find_crossings(polyline: Sequence[XY], hazards: Geohazards) -> list[HazardCrossing]:
    """Intersect an alignment with fault traces and ground-movement zones."""
    line = list(polyline)
    out: list[HazardCrossing] = []
    ch0 = 0.0
    seg_start: list[float] = []
    for a, b in itertools.pairwise(line):
        seg_start.append(ch0)
        ch0 += math.dist(a, b)
    for f in hazards.faults:
        for i, (a, b) in enumerate(itertools.pairwise(line)):
            px, py = b[0] - a[0], b[1] - a[1]
            for c, d in itertools.pairwise(f.trace):
                sx, sy = d[0] - c[0], d[1] - c[1]
                den = px * sy - py * sx
                if den == 0:
                    continue
                t = ((c[0] - a[0]) * sy - (c[1] - a[1]) * sx) / den
                u = ((c[0] - a[0]) * py - (c[1] - a[1]) * px) / den
                if 0 <= t < 1 and 0 <= u <= 1:
                    beta = _angle_ccw(px, py, sx, sy)
                    pt = (a[0] + t * px, a[1] + t * py)
                    mv = fault_movement(f.slip_m, beta, dip_deg=f.dip_deg, rake_deg=f.rake_deg)
                    out.append(HazardCrossing(
                        hazard=f.name, kind="fault",
                        chainage_m=seg_start[i] + t * math.hypot(px, py), point=pt,
                        crossing_angle_deg=round(beta, 2), movement=mv, slip_m=f.slip_m,
                        dip_deg=f.dip_deg, rake_deg=f.rake_deg,
                    ))  # fmt: skip
    if hazards.zones:
        step = 5.0
        pts, chs = [], []
        for i, (a, b) in enumerate(itertools.pairwise(line)):
            seg = math.dist(a, b)
            n = max(1, math.ceil(seg / step))
            tt = (np.arange(n) + 0.5) / n
            pts.append(np.column_stack([a[0] + (b[0] - a[0]) * tt, a[1] + (b[1] - a[1]) * tt]))
            chs.append(seg_start[i] + tt * seg)
        xy, ch = np.vstack(pts), np.concatenate(chs)
        for z in hazards.zones:
            ins = _inside(xy[:, 0], xy[:, 1], z.polygon)
            if not ins.any():
                continue
            edges = np.flatnonzero(np.diff(np.r_[0, ins.astype(int), 0]))
            for s0, s1 in zip(edges[0::2], edges[1::2], strict=True):
                (x0, y0), (x1, y1) = xy[s0], xy[s1 - 1]
                width = float(ch[s1 - 1] - ch[s0]) + step
                mid = (s0 + s1 - 1) // 2
                dx, dy = x1 - x0, y1 - y0
                norm = math.hypot(dx, dy) or 1.0
                ux, uy = dx / norm, dy / norm  # pipe direction through the zone
                if z.kind == "subsidence":
                    mv = settlement(z.displacement_m, width)
                    ang = None
                else:
                    assert z.direction_azimuth_deg is not None
                    az = math.radians(z.direction_azimuth_deg)
                    mx, my = math.sin(az), math.cos(az)
                    ang = round(_angle_ccw(ux, uy, mx, my), 2) % 360
                    mv = GroundMovement(
                        kind=z.pattern, width_m=width,
                        axial_m=z.displacement_m * (mx * ux + my * uy),
                        transverse_m=z.displacement_m * (-mx * uy + my * ux),
                        label=f"{z.kind} {z.displacement_m:g} m over {width:.0f} m",
                    )  # fmt: skip
                out.append(HazardCrossing(
                    hazard=z.name, kind=z.kind, chainage_m=float(ch[mid]),
                    point=(float(xy[mid, 0]), float(xy[mid, 1])), crossing_angle_deg=ang,
                    width_m=width, movement=mv,
                ))  # fmt: skip
    return sorted(out, key=lambda c: c.chainage_m)


# ------------------------------------------------------------------ assessment


class BurialBasis(_Model):
    """Soil and cover used for the springs (state these as assumptions)."""

    soil: Soil = Field(default_factory=lambda: Soil(
        soil_type=SoilType.DENSE_SAND, unit_weight_n_m3=18e3, friction_angle_deg=36))  # fmt: skip
    cover_m: float = Field(default=1.2, gt=0)
    coating: Coating = Coating.POLYETHYLENE


class HazardCheck(_Model):
    crossing: HazardCrossing
    wall_mm: float
    converged: bool
    max_tensile_strain: float
    max_compressive_strain: float
    utilisation: float
    passed: bool
    movement_at_limit_m: float | None
    notes: tuple[str, ...]
    mitigation: str = ""
    mitigation_wall_mm: float | None = None
    mitigation_length_m: float = 0.0
    mitigation_cost: float = 0.0
    """Installed cost of the heavier wall over the affected length (INR, with indirects)."""
    best_crossing_angle_deg: float | None = None


class GeohazardAssessment(_Model):
    checks: tuple[HazardCheck, ...]
    basis: BurialBasis
    level: PerformanceLevel

    @property
    def passed(self) -> bool:
        return all(c.passed or c.mitigation_wall_mm is not None for c in self.checks)

    @property
    def unmitigated(self) -> list[HazardCheck]:
        return [c for c in self.checks if not c.passed and c.mitigation_wall_mm is None]

    @property
    def worst_utilisation(self) -> float:
        return max((c.utilisation for c in self.checks), default=0.0)

    @property
    def mitigation_cost(self) -> float:
        return sum(c.mitigation_cost for c in self.checks)


def affected_length_m(crossing: HazardCrossing, od_m: float) -> float:
    """Pipe length given heavier wall: the zone plus a strain-decay length each side."""
    margin = max(100.0, 50 * od_m)
    return (crossing.width_m if crossing.kind != "fault" else 0.0) + 2 * margin


def assess_geohazards(
    crossings: Sequence[HazardCrossing],
    *,
    nps: str | float,
    grade: str,
    wall_m: float | Sequence[float],
    pressure_pa: float,
    rates: UnitRates,
    basis: BurialBasis | None = None,
    level: PerformanceLevel = PerformanceLevel.NORMAL_OPERABILITY,
    process: str = "SAWL",
    mitigate: bool = True,
    max_d_over_t_mitigation: float = 20.0,
) -> GeohazardAssessment:
    """FE strain check for each crossing; heavier wall and best angle where it fails.

    ``wall_m`` is one wall for all crossings, or one per crossing (e.g. the
    location-class wall at each chainage).
    """
    from pipeline_analytics.cost.boq import with_indirects
    from pipeline_analytics.mechanical import pipe_from_library
    from pipeline_analytics.pipespec import default_library

    basis = basis or BurialBasis()
    walls = [wall_m] * len(crossings) if isinstance(wall_m, int | float) else list(wall_m)
    if len(walls) != len(crossings):
        raise ValueError("one wall per crossing is needed")
    lib = default_library()
    size = lib.size(nps)
    std_walls = sorted({w.wt_mm for w in size.wall_thicknesses})
    checks = []
    for cr, wt in zip(crossings, walls, strict=True):
        pipe = pipe_from_library(nps, wt * 1000, grade)
        springs = soil_springs(pipe.od_m, basis.cover_m + pipe.od_m / 2, basis.soil, basis.coating)
        kw = {"grade": grade, "pressure": pressure_pa, "level": level}
        a = analyse_pgd(pipe, springs, cr.movement, **kw)  # type: ignore[arg-type]
        chk = HazardCheck(
            crossing=cr, wall_mm=round(wt * 1000, 2), converged=a.fe.converged,
            max_tensile_strain=a.fe.max_tensile_strain,
            max_compressive_strain=a.fe.max_compressive_strain,
            utilisation=a.utilisation if a.fe.converged else math.inf, passed=a.passed,
            movement_at_limit_m=a.movement_at_limit_m, notes=a.notes,
        )  # fmt: skip
        if not chk.passed and mitigate:
            chk = _mitigate(chk, pipe, springs, kw, std_walls, size.od_mm, grade, nps,
                            max_d_over_t_mitigation, rates, process, with_indirects)  # fmt: skip
        checks.append(chk)
    return GeohazardAssessment(checks=tuple(checks), basis=basis, level=level)


def _mitigate(chk, pipe, springs, kw, std_walls, od_mm, grade, nps, max_dt, rates, process,  # type: ignore[no-untyped-def]
              with_indirects) -> HazardCheck:  # fmt: skip
    from pipeline_analytics.mechanical import pipe_from_library

    upd: dict[str, object] = {}
    cr = chk.crossing
    if cr.kind == "fault":
        assert cr.slip_m is not None
        sw = crossing_angle_sweep(pipe, springs, cr.slip_m, PRACTICAL_ANGLES_DEG,
                                  dip_deg=cr.dip_deg, rake_deg=cr.rake_deg, **kw)  # fmt: skip
        upd["best_crossing_angle_deg"] = sw.best_angle_deg
    for w in std_walls:
        if w <= chk.wall_mm + 1e-6 or od_mm / w < max_dt:
            continue
        p2 = pipe_from_library(nps, w, grade)
        a2 = analyse_pgd(p2, springs, cr.movement, **kw)
        if a2.passed:
            length = affected_length_m(cr, pipe.od_m)
            dt = (
                math.pi
                * 7850
                * length
                / 1000
                * ((p2.od_m - p2.wt_m) * p2.wt_m - (pipe.od_m - pipe.wt_m) * pipe.wt_m)
            )
            direct = dt * rates.pipe_rate_per_tonne(grade, process)
            upd |= {
                "mitigation": f"heavier wall {w:g} mm over {length:.0f} m "
                f"(strain UR {a2.utilisation:.2f})",
                "mitigation_wall_mm": w, "mitigation_length_m": length,
                "mitigation_cost": with_indirects(direct, 0.0, rates),
            }  # fmt: skip
            break
    else:
        upd["mitigation"] = (
            "no standard wall passes: realign the crossing"
            + (f" (best angle {upd['best_crossing_angle_deg']:g} deg to the strike)"
               if "best_crossing_angle_deg" in upd else "")
            + " or design a special crossing (loose backfill trench, shell analysis)"
        )  # fmt: skip
    return HazardCheck.model_validate({**chk.model_dump(), **upd})


# ------------------------------------------------------------------ realignment


def crossing_waypoints(
    crossing: HazardCrossing, fault: Fault, target_angle_deg: float, lead_m: float = 1500.0
) -> tuple[XY, XY]:
    """Two waypoints either side of the crossing point so the pipe crosses the fault at
    ``target_angle_deg`` (pipe direction to strike, counter-clockwise, 0-180).

    Reversing the pipe direction changes the angle by 180 deg and swaps the moving
    block, which leaves the strains unchanged, so the waypoints keep the original
    direction of travel.
    """
    if crossing.kind != "fault" or crossing.crossing_angle_deg is None:
        raise ValueError("waypoints apply to fault crossings")
    cx, cy = crossing.point
    best: tuple[float, float, float] | None = None
    for c, d in itertools.pairwise(fault.trace):  # strike of the crossed trace segment
        dist = float(_dist_to_segment(np.array(cx), np.array(cy), c[0], c[1], d[0], d[1]))
        if best is None or dist < best[0]:
            best = (dist, d[0] - c[0], d[1] - c[1])
    assert best is not None
    n = math.hypot(best[1], best[2])
    sx, sy = best[1] / n, best[2] / n

    def rot(x: float, y: float, deg: float) -> tuple[float, float]:
        a = math.radians(deg)
        return x * math.cos(a) - y * math.sin(a), x * math.sin(a) + y * math.cos(a)

    ox, oy = rot(sx, sy, -crossing.crossing_angle_deg)  # current pipe direction
    px, py = rot(sx, sy, -target_angle_deg)
    if px * ox + py * oy < 0:
        px, py = -px, -py
    return (cx - lead_m * px, cy - lead_m * py), (cx + lead_m * px, cy + lead_m * py)


def realign_fault_crossing(
    surface: CostSurface,
    start: XY,
    end: XY,
    crossing: HazardCrossing,
    fault: Fault,
    target_angle_deg: float,
    lead_m: float = 1500.0,
    waypoints_before: Sequence[XY] = (),
    waypoints_after: Sequence[XY] = (),
) -> list[XY]:
    """Least-cost route that crosses ``fault`` on a straight leg at ``target_angle_deg``.

    The approach and exit are least-cost paths to and from two waypoints placed
    ``lead_m`` either side of the original crossing point; the leg between them is
    straight, so the router cannot bend the crossing back to the cheapest
    (perpendicular) direction through the hazard band.
    """
    from pipeline_analytics.routing.extract import smooth_path_polyline
    from pipeline_analytics.routing.pathfinding import least_cost_path

    w1, w2 = crossing_waypoints(crossing, fault, target_angle_deg, lead_m)
    a = least_cost_path(surface, start, w1, waypoints_before)
    b = least_cost_path(surface, w2, end, waypoints_after)
    first = smooth_path_polyline(surface, a.cells)
    last = smooth_path_polyline(surface, b.cells)
    return [*first[:-1], w1, w2, *last[1:]]


__all__ = [
    "BurialBasis",
    "Fault",
    "GeohazardAssessment",
    "Geohazards",
    "GroundZone",
    "HazardCheck",
    "HazardCrossing",
    "affected_length_m",
    "assess_geohazards",
    "crossing_waypoints",
    "find_crossings",
    "realign_fault_crossing",
]
