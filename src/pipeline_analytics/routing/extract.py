"""Turn an alignment (polyline) into design and cost inputs.

Works for corridors from the router and for alignments the user supplies (for
example, digitised from a survey). The polyline is sampled every 1/8 cell; each
sample's length is attributed to the layers of the cell it falls in.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from pipeline_analytics.codes.class_location import ClassSegment, classify_route
from pipeline_analytics.core.jurisdiction import Service
from pipeline_analytics.cost.boq import RouteQuantities
from pipeline_analytics.cost.rates import CrossingType, LandUse, Terrain
from pipeline_analytics.economics.option import OptionInput
from pipeline_analytics.hydraulics.profile import Profile
from pipeline_analytics.mechanical.pressure import LocationClass
from pipeline_analytics.routing.aoi import LINEAR_KINDS, RIVER, LandCover, to_buildings
from pipeline_analytics.routing.cost_surface import LAND_USES, TERRAINS, CostSurface
from pipeline_analytics.routing.geohazards import HazardCrossing, find_crossings
from pipeline_analytics.routing.geometry import chainage_offset, route_length

XY = tuple[float, float]


def simplify(points: Sequence[XY], tolerance_m: float) -> list[XY]:
    """Douglas-Peucker simplification (keeps end points)."""
    pts = list(points)
    if len(pts) < 3:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i, j = stack.pop()
        (ax, ay), (bx, by) = pts[i], pts[j]
        seg = math.hypot(bx - ax, by - ay)
        best, idx = 0.0, -1
        for k in range(i + 1, j):
            px, py = pts[k]
            d = (
                abs((bx - ax) * (ay - py) - (ax - px) * (by - ay)) / seg
                if seg
                else math.hypot(px - ax, py - ay)
            )
            if d > best:
                best, idx = d, k
        if best > tolerance_m:
            keep[idx] = True
            stack.extend([(i, idx), (idx, j)])
    return [p for p, k in zip(pts, keep, strict=True) if k]


@dataclass(frozen=True)
class RouteExtraction:
    polyline: tuple[XY, ...]
    length_m: float
    profile: Profile
    terrain_m: dict[Terrain, float]
    land_use_m: dict[LandUse, float]
    crossings: dict[str, int]
    """Entries into each linear feature kind (river, national_highway, ...)."""
    hdd_m: float
    wet_length_m: float
    protected_m: float
    hazard_m: dict[int, float]
    corridor_shared_m: float
    class_segments: tuple[ClassSegment, ...]
    route: RouteQuantities
    geohazard_crossings: tuple[HazardCrossing, ...] = ()
    """Faults and ground-movement zones this alignment crosses (needs AOI.geohazards)."""

    def location_class_at(self, chainage_m: float) -> LocationClass:
        for s in self.class_segments:
            if s.start_m - 1e-6 <= chainage_m <= s.end_m + 1e-6:
                return s.location_class
        return LocationClass.CLASS_1_DIV_2

    def option_input(self, base: OptionInput) -> OptionInput:
        """``base`` with this alignment's profile, classes and quantities (validated rebuild)."""
        data = base.model_dump()
        data.update(
            profile=self.profile.model_dump(),
            class_segments=[s.model_dump() for s in self.class_segments],
            route=self.route.model_dump(),
            wet_length_m=self.wet_length_m,
        )
        return OptionInput.model_validate(data)


def extract_route(
    surface: CostSurface,
    polyline: Sequence[XY],
    *,
    profile_step_m: float = 1000.0,
    terminals: int = 2,
    scraper_spacing_m: float | None = 150_000.0,
) -> RouteExtraction:
    aoi, g = surface.aoi, surface.grid
    line = list(polyline)
    length = route_length(line)
    if length <= 0:
        raise ValueError("alignment has zero length")

    # sample the line every 1/8 cell
    step = g.cell_m / 8
    xs, ys, seg_len, chain = [], [], [], []
    ch0 = 0.0
    for (ax, ay), (bx, by) in itertools.pairwise(line):
        d = math.hypot(bx - ax, by - ay)
        n = max(1, math.ceil(d / step))
        t = (np.arange(n) + 0.5) / n
        xs.append(ax + (bx - ax) * t)
        ys.append(ay + (by - ay) * t)
        seg_len.append(np.full(n, d / n))
        chain.append(ch0 + d * t)
        ch0 += d
    x, y, dl, _ch = map(np.concatenate, (xs, ys, seg_len, chain))
    r, c = g.rc_array(x, y)

    def sum_by(index: np.ndarray, labels: Sequence[object], weights: np.ndarray) -> dict:  # type: ignore[type-arg]
        tot = np.bincount(index, weights=weights, minlength=len(labels))
        return {lab: float(v) for lab, v in zip(labels, tot, strict=True) if v > 0}

    water = aoi.layer("water") | (aoi.land_cover == LandCover.WATER)
    in_water = water[r, c]
    hdd_m = float(dl[in_water].sum())
    # HDD lengths are neither laid conventionally nor on right-of-use land
    dry = np.where(in_water, 0.0, dl)
    terrain_m = sum_by(surface.terrain[r, c], TERRAINS, dry)
    land_use_m = sum_by(surface.land_use[r, c], LAND_USES, dry)
    wet = float(dl[(aoi.land_cover[r, c] == LandCover.WETLAND)].sum())
    protected_m = float(dl[aoi.layer("protected")[r, c]].sum())
    corridor_m = float(dl[aoi.layer("corridor")[r, c]].sum())
    haz = aoi.hazard if aoi.hazard is not None else np.zeros(g.shape, dtype=int)
    hazard_m = {int(k): v for k, v in sum_by(haz[r, c], [0, 1, 2, 3], dl).items() if k}

    # crossings: entries into each feature mask along the cell sequence
    cells = [(int(r[0]), int(c[0]))]
    for rr, cc in zip(r[1:], c[1:], strict=True):
        if (rr, cc) != cells[-1]:
            cells.append((int(rr), int(cc)))
    crossings: dict[str, int] = {}
    for kind in LINEAR_KINDS:
        m = aoi.line_mask(kind)
        if not m.any():
            continue
        on = [bool(m[a, b]) for a, b in cells]
        n = sum(1 for prev, cur in zip([False, *on[:-1]], on, strict=True) if cur and not prev)
        if n:
            crossings[kind] = n

    # profile at regular chainage plus every vertex
    vert_ch = np.cumsum([0.0, *(math.dist(a, b) for a, b in itertools.pairwise(line))])
    stations = sorted(
        set(np.linspace(0, length, max(2, round(length / profile_step_m) + 1))) | set(vert_ch)
    )
    px = np.interp(stations, vert_ch, [p[0] for p in line])
    py = np.interp(stations, vert_ch, [p[1] for p in line])
    pr, pc = g.rc_array(px, py)
    profile = Profile(
        chainage_m=tuple(float(s) for s in stations),
        elevation_m=tuple(float(e) for e in aoi.elevation_m[pr, pc]),
    )

    # buildings -> chainage/offset -> location classes (gas)
    segs: tuple[ClassSegment, ...] = ()
    if surface.params.service is Service.GAS:
        near = list(aoi.buildings)
        co = [chainage_offset(line, (b.x, b.y)) for b in near]
        segs = tuple(classify_route(length, to_buildings(near, co), step_m=min(50.0, length / 10)))

    route = RouteQuantities(
        terrain_m=terrain_m,
        land_use_m=land_use_m,
        crossings={CrossingType(k): v for k, v in crossings.items() if k != RIVER},
        hdd_m=hdd_m,
        open_cut_river_m=crossings.get(RIVER, 0) * surface.params.narrow_river_width_m,
        terminals=terminals,
        scraper_stations=(
            max(0, math.ceil(length / scraper_spacing_m) - 1) if scraper_spacing_m else 0
        ),
    )
    return RouteExtraction(
        polyline=tuple(line),
        length_m=length,
        profile=profile,
        terrain_m=terrain_m,
        land_use_m=land_use_m,
        crossings=crossings,
        hdd_m=hdd_m,
        wet_length_m=wet,
        protected_m=protected_m,
        hazard_m=hazard_m,
        corridor_shared_m=corridor_m,
        class_segments=segs,
        route=route,
        geohazard_crossings=tuple(find_crossings(line, aoi.geohazards)) if aoi.geohazards else (),
    )


def smooth_path_polyline(
    surface: CostSurface, cells: Sequence[tuple[int, int]], tolerance_cells: float = 1.0
) -> list[XY]:
    """Cell-centre path simplified within ``tolerance_cells``, never through excluded cells.

    If simplifying would cut through an excluded cell, the raw cell path is used.
    """
    raw = [surface.grid.center(r, c) for r, c in cells]
    simple = simplify(raw, tolerance_cells * surface.grid.cell_m)
    blocked = ~np.isfinite(surface.cell_cost)
    if not blocked.any():
        return simple
    g = surface.grid
    for a, b in itertools.pairwise(simple):
        n = max(2, math.ceil(math.dist(a, b) / (g.cell_m / 8)))
        t = np.linspace(0, 1, n)
        rr, cc = g.rc_array(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
        if blocked[rr, cc].any():
            return raw
    return simple
