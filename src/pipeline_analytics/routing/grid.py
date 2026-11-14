"""Raster grid in a projected (metre) coordinate system.

Row 0 is the northern edge. Cell (r, c) covers
``x0 + c*cell .. x0 + (c+1)*cell`` and ``y0 - (r+1)*cell .. y0 - r*cell``.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray


@dataclass(frozen=True)
class Grid:
    x0: float
    """x of the west edge (m)."""
    y0: float
    """y of the north edge (m)."""
    cell_m: float
    nrows: int
    ncols: int
    crs: str = "EPSG:32643"
    """Projected CRS, e.g. UTM zone 43N (EPSG:32643) for much of western India."""

    def __post_init__(self) -> None:
        if self.cell_m <= 0 or self.nrows < 2 or self.ncols < 2:
            raise ValueError("grid needs a positive cell size and at least 2 x 2 cells")

    @property
    def shape(self) -> tuple[int, int]:
        return self.nrows, self.ncols

    def center(self, r: float, c: float) -> tuple[float, float]:
        return self.x0 + (c + 0.5) * self.cell_m, self.y0 - (r + 0.5) * self.cell_m

    def rc(self, x: float, y: float) -> tuple[int, int]:
        """Cell containing (x, y); raises if outside the grid."""
        c = int(np.floor((x - self.x0) / self.cell_m))
        r = int(np.floor((self.y0 - y) / self.cell_m))
        if not (0 <= r < self.nrows and 0 <= c < self.ncols):
            raise ValueError(f"point ({x:.0f}, {y:.0f}) is outside the grid")
        return r, c

    def rc_array(
        self, xs: NDArray[np.float64], ys: NDArray[np.float64]
    ) -> tuple[NDArray[np.intp], NDArray[np.intp]]:
        """Vectorised cell lookup, clipped to the grid."""
        c = np.clip(np.floor((xs - self.x0) / self.cell_m).astype(np.intp), 0, self.ncols - 1)
        r = np.clip(np.floor((self.y0 - ys) / self.cell_m).astype(np.intp), 0, self.nrows - 1)
        return r, c

    def centers(self) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
        """Arrays (X, Y) of cell-centre coordinates, shape (nrows, ncols)."""
        xs = self.x0 + (np.arange(self.ncols) + 0.5) * self.cell_m
        ys = self.y0 - (np.arange(self.nrows) + 0.5) * self.cell_m
        return np.meshgrid(xs, ys)

    def empty(self, dtype: type = bool) -> NDArray:  # type: ignore[type-arg]
        return np.zeros(self.shape, dtype=dtype)
