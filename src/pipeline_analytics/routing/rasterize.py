"""Burn vector features into a grid without GDAL.

Lines use a *supercover* (4-connected) rasterisation: every cell the line
passes through is marked. A path moving between 8-connected neighbours
therefore cannot cross the line diagonally without stepping on a marked cell,
which is what makes crossing counts exact.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Iterable, Sequence

import numpy as np
from numpy.typing import NDArray

from pipeline_analytics.routing.grid import Grid

XY = tuple[float, float]


def _supercover(grid: Grid, a: XY, b: XY, out: NDArray[np.bool_]) -> None:
    # sample densely (quarter cell) and add the 4-connected corner cells between samples
    length = math.dist(a, b)
    n = max(2, int(length / (grid.cell_m / 4)) + 2)
    t = np.linspace(0.0, 1.0, n)
    xs = a[0] + (b[0] - a[0]) * t
    ys = a[1] + (b[1] - a[1]) * t
    inside = (
        (xs >= grid.x0)
        & (xs < grid.x0 + grid.ncols * grid.cell_m)
        & (ys <= grid.y0)
        & (ys > grid.y0 - grid.nrows * grid.cell_m)
    )
    if not inside.any():
        return
    r, c = grid.rc_array(xs, ys)
    out[r[inside], c[inside]] = True
    # where consecutive samples jump diagonally, mark one of the corner cells
    dr, dc = np.diff(r), np.diff(c)
    diag = (np.abs(dr) == 1) & (np.abs(dc) == 1) & inside[1:] & inside[:-1]
    out[r[:-1][diag], c[1:][diag]] = True


def rasterize_lines(grid: Grid, lines: Iterable[Sequence[XY]]) -> NDArray[np.bool_]:
    out = grid.empty(bool)
    for line in lines:
        for a, b in itertools.pairwise(line):
            _supercover(grid, a, b, out)
    return out


def rasterize_polygons(grid: Grid, polygons: Iterable[Sequence[XY]]) -> NDArray[np.bool_]:
    """Cells whose centre is inside any polygon (even-odd rule; outer rings only)."""
    out = grid.empty(bool)
    xs, ys = grid.centers()
    for poly in polygons:
        px = np.array([p[0] for p in poly])
        py = np.array([p[1] for p in poly])
        r0, c0 = grid.rc_array(np.array([px.min(), px.max()]), np.array([py.max(), py.min()]))
        rs, re = int(r0[0]), int(r0[1]) + 1
        cs, ce = int(c0[0]), int(c0[1]) + 1
        x, y = xs[rs:re, cs:ce], ys[rs:re, cs:ce]
        inside = np.zeros(x.shape, dtype=bool)
        j = len(px) - 1
        for i in range(len(px)):
            cond = (py[i] > y) != (py[j] > y)
            with np.errstate(divide="ignore", invalid="ignore"):
                x_cross = (px[j] - px[i]) * (y - py[i]) / (py[j] - py[i]) + px[i]
            inside ^= cond & (x < x_cross)
            j = i
        out[rs:re, cs:ce] |= inside
    return out


def density_per_km2(
    grid: Grid, points: Iterable[tuple[float, float, float]], radius_m: float = 450.0
) -> NDArray[np.float64]:
    """Sum of point weights within ``radius_m`` of each cell centre, per km^2.

    With the default radius the circle area (~0.64 km^2) equals a 1.6 km x 400 m
    class location unit, so ~17 units/km^2 is the Class 2 threshold and ~72 the
    Class 3 threshold.
    """
    counts = grid.empty(float)
    k = math.ceil(radius_m / grid.cell_m)
    offs = [
        (dr, dc)
        for dr in range(-k, k + 1)
        for dc in range(-k, k + 1)
        if math.hypot(dr, dc) * grid.cell_m <= radius_m
    ]
    for x, y, w in points:
        try:
            r, c = grid.rc(x, y)
        except ValueError:
            continue
        for dr, dc in offs:
            rr, cc = r + dr, c + dc
            if 0 <= rr < grid.nrows and 0 <= cc < grid.ncols:
                counts[rr, cc] += w
    area_km2 = len(offs) * grid.cell_m**2 / 1e6
    return counts / area_km2
