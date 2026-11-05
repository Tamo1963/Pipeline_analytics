"""Least-cost path and alternative corridors on the cost surface.

Graph: every cell is a node joined to its 8 neighbours by *directed* edges
(climbing and descending can cost differently). Edge i -> j costs

    d x (c_i + c_j) / 2                       cell costs (INR/m) x length
  + sum_k E_k [j on feature k and i not]       lump crossing cost per entry
  + climb x max(0, z_j - z_i) + descent x max(0, z_i - z_j)
  + length x d                                 OPEX-equivalent per metre

Linear features are rasterised 4-connected, so an 8-connected path cannot
cross one without entering it; each crossing is paid exactly once.

Alternatives use the penalty method: after each corridor, cells within
``buffer_m`` of it get dearer by ``penalty`` (default 2.0), the search is repeated, and a new
corridor is kept only if at most ``max_overlap`` of it lies within the buffer
of a corridor already found. All corridors are then re-costed on the original
surface and sorted by cost.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from scipy import ndimage
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra

from pipeline_analytics.routing.aoi import LINEAR_KINDS
from pipeline_analytics.routing.cost_surface import CostSurface

XY = tuple[float, float]
_OFFSETS = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


@dataclass(frozen=True)
class Path:
    cells: tuple[tuple[int, int], ...]
    cost: float
    """INR on the original surface (CAPEX-equivalent + OPEX-equivalent penalties)."""

    def polyline(self, surface: CostSurface) -> list[XY]:
        return [surface.grid.center(r, c) for r, c in self.cells]

    def mask(self, shape: tuple[int, int]) -> NDArray[np.bool_]:
        m = np.zeros(shape, dtype=bool)
        rr, cc = zip(*self.cells, strict=True)
        m[list(rr), list(cc)] = True
        return m


def _edges(
    surface: CostSurface, cell_cost: NDArray[np.float64]
) -> tuple[NDArray[np.intp], NDArray[np.intp], NDArray[np.float64]]:
    g = surface.grid
    nr, nc = g.shape
    idx = np.arange(nr * nc).reshape(nr, nc)
    z = surface.aoi.elevation_m
    p = surface.params
    masks = {k: surface.aoi.line_mask(k) for k in LINEAR_KINDS}
    src_all, dst_all, w_all = [], [], []
    for dr, dc in _OFFSETS:
        rs = slice(max(0, -dr), nr - max(0, dr))
        cs = slice(max(0, -dc), nc - max(0, dc))
        rd = slice(max(0, dr), nr - max(0, -dr))
        cd = slice(max(0, dc), nc - max(0, -dc))
        d = g.cell_m * math.hypot(dr, dc)
        w = d * (cell_cost[rs, cs] + cell_cost[rd, cd]) / 2 + p.length_per_m * d
        dz = z[rd, cd] - z[rs, cs]
        w = w + p.climb_per_m * np.maximum(dz, 0) + p.descent_per_m * np.maximum(-dz, 0)
        for k, m in masks.items():
            if m.any():
                w = w + surface.entry_cost[k] * (m[rd, cd] & ~m[rs, cs])
        ok = np.isfinite(w)
        src_all.append(idx[rs, cs][ok])
        dst_all.append(idx[rd, cd][ok])
        w_all.append(w[ok])
    return np.concatenate(src_all), np.concatenate(dst_all), np.concatenate(w_all)


def _graph(surface: CostSurface, cell_cost: NDArray[np.float64]) -> csr_matrix:
    src, dst, w = _edges(surface, cell_cost)
    n = surface.grid.nrows * surface.grid.ncols
    return csr_matrix((w, (src, dst)), shape=(n, n))


def _solve(
    graph: csr_matrix, ncols: int, a: tuple[int, int], b: tuple[int, int]
) -> list[tuple[int, int]]:
    s, t = a[0] * ncols + a[1], b[0] * ncols + b[1]
    dist, pred = dijkstra(graph, directed=True, indices=s, return_predecessors=True)
    if not np.isfinite(dist[t]):
        raise ValueError("no feasible path between the given points (exclusions block it)")
    out = [t]
    while out[-1] != s:
        out.append(int(pred[out[-1]]))
    return [(i // ncols, i % ncols) for i in reversed(out)]


def path_cost(surface: CostSurface, cells: Sequence[tuple[int, int]]) -> float:
    """Cost of a cell sequence on the surface (same edge rule as the search)."""
    p = surface.params
    z = surface.aoi.elevation_m
    masks = {k: surface.aoi.line_mask(k) for k in LINEAR_KINDS}
    total = 0.0
    for (r0, c0), (r1, c1) in itertools.pairwise(cells):
        d = surface.grid.cell_m * math.hypot(r1 - r0, c1 - c0)
        total += d * (surface.cell_cost[r0, c0] + surface.cell_cost[r1, c1]) / 2
        total += p.length_per_m * d
        dz = z[r1, c1] - z[r0, c0]
        total += p.climb_per_m * max(dz, 0.0) + p.descent_per_m * max(-dz, 0.0)
        for k, m in masks.items():
            if m[r1, c1] and not m[r0, c0]:
                total += surface.entry_cost[k]
    return float(total)


def least_cost_path(surface: CostSurface, start: XY, end: XY, waypoints: Sequence[XY] = ()) -> Path:
    """Least-cost path from ``start`` to ``end`` through ``waypoints`` in order."""
    g = surface.grid
    pts = [g.rc(*start), *(g.rc(*w) for w in waypoints), g.rc(*end)]
    graph = _graph(surface, surface.cell_cost)
    cells: list[tuple[int, int]] = [pts[0]]
    for a, b in itertools.pairwise(pts):
        cells.extend(_solve(graph, g.ncols, a, b)[1:])
    return Path(cells=tuple(cells), cost=path_cost(surface, cells))


def overlap(
    path: Path, others: Sequence[Path], shape: tuple[int, int], buffer_cells: float
) -> float:
    """Share of ``path`` cells within ``buffer_cells`` of any of ``others``."""
    if not others:
        return 0.0
    near = np.zeros(shape, dtype=bool)
    for o in others:
        near |= ndimage.distance_transform_edt(~o.mask(shape)) <= buffer_cells
    rr, cc = zip(*path.cells, strict=True)
    return float(near[list(rr), list(cc)].mean())


def alternative_corridors(
    surface: CostSurface,
    start: XY,
    end: XY,
    k: int = 4,
    *,
    buffer_m: float | None = None,
    penalty: float = 2.0,
    max_overlap: float = 0.5,
    max_tries: int | None = None,
    waypoints: Sequence[XY] = (),
) -> list[Path]:
    """Up to ``k`` distinct corridors, cheapest first (costed on the original surface).

    ``buffer_m`` defaults to 7.5 % of the straight-line distance (at least two
    cells), wide enough to give genuinely different routes rather than parallel
    lanes.
    """
    g = surface.grid
    pts = [g.rc(*start), *(g.rc(*w) for w in waypoints), g.rc(*end)]
    if buffer_m is None:
        buffer_m = max(2 * g.cell_m, 0.075 * math.dist(start, end))
    buf = buffer_m / g.cell_m
    cost = surface.cell_cost.copy()
    found: list[Path] = []
    for _ in range(max_tries or 3 * k):
        graph = _graph(surface, cost)
        cells: list[tuple[int, int]] = [pts[0]]
        try:
            for a, b in itertools.pairwise(pts):
                cells.extend(_solve(graph, g.ncols, a, b)[1:])
        except ValueError:
            break
        cand = Path(cells=tuple(cells), cost=path_cost(surface, cells))
        if overlap(cand, found, g.shape, buf) <= max_overlap:
            found.append(cand)
            if len(found) == k:
                break
        near = ndimage.distance_transform_edt(~cand.mask(g.shape)) <= buf
        cost = np.where(near, cost * penalty, cost)
    return sorted(found, key=lambda p: p.cost)
