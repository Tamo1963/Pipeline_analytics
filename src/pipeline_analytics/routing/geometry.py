"""Planar route geometry: chainage and offset of features along a centreline.

Coordinates are projected metres (e.g. UTM). Reprojection from latitude and
longitude is done when routes are loaded (Phase 4); this module only needs
x/y in metres.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Sequence

Point = tuple[float, float]


def route_length(route: Sequence[Point]) -> float:
    return sum(math.dist(a, b) for a, b in itertools.pairwise(route))


def chainage_offset(route: Sequence[Point], point: Point) -> tuple[float, float]:
    """Project ``point`` onto the polyline ``route``.

    Returns ``(chainage, offset)``: distance along the route to the nearest
    point on it, and the perpendicular distance (positive to the left of the
    direction of travel, negative to the right).
    """
    if len(route) < 2:
        raise ValueError("route needs at least two vertices")
    best: tuple[float, float, float] | None = None  # (distance, chainage, signed offset)
    start = 0.0
    px, py = point
    for (ax, ay), (bx, by) in itertools.pairwise(route):
        dx, dy = bx - ax, by - ay
        seg = math.hypot(dx, dy)
        if seg == 0:
            continue
        t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg**2))
        cx, cy = ax + t * dx, ay + t * dy
        dist = math.hypot(px - cx, py - cy)
        side = math.copysign(1.0, dx * (py - ay) - dy * (px - ax)) if dist else 1.0
        if best is None or dist < best[0] - 1e-9:
            best = (dist, start + t * seg, side * dist)
        start += seg
    assert best is not None
    return best[1], best[2]
