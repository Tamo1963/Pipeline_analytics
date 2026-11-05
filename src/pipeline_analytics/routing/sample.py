"""A synthetic 40 km x 24 km area of interest for tests and demos.

Not real terrain. It is built so that the cheapest route is a genuine trade-off:

* a north-south ridge (up to ~300 m relief) with a low saddle in the north;
* a protected forest block on the southern ridge (excluded);
* a 400 m wide river near the east end (HDD), plus two streams;
* a national highway, a state highway and a railway to cross;
* a town (Class 3 density) and villages near the straight line;
* a wetland, a fault zone (high hazard) and an existing power-line ROW in the
  north that can be shared.

Coordinates are UTM 43N (EPSG:32643) metres around 73.1 E, 22.6 N.
"""

from __future__ import annotations

import numpy as np

from pipeline_analytics.cost.rates import CrossingType
from pipeline_analytics.routing.aoi import AOI, RIVER, BuildingPoint, LandCover
from pipeline_analytics.routing.grid import Grid
from pipeline_analytics.routing.rasterize import rasterize_polygons

X0, Y0 = 300_000.0, 2_512_000.0
WIDTH, HEIGHT = 40_000.0, 24_000.0
START = (X0 + 1_000.0, Y0 - 12_000.0)
END = (X0 + 39_000.0, Y0 - 12_000.0)


def _rect(x1: float, y1: float, x2: float, y2: float) -> list[tuple[float, float]]:
    """Rectangle in local km offsets from the north-west corner (y downwards)."""
    return [
        (X0 + x1 * 1000, Y0 - y1 * 1000),
        (X0 + x2 * 1000, Y0 - y1 * 1000),
        (X0 + x2 * 1000, Y0 - y2 * 1000),
        (X0 + x1 * 1000, Y0 - y2 * 1000),
    ]


def _pt(xk: float, yk: float) -> tuple[float, float]:
    return X0 + xk * 1000, Y0 - yk * 1000


def sample_aoi(cell_m: float = 100.0) -> AOI:
    grid = Grid(x0=X0, y0=Y0, cell_m=cell_m, nrows=int(HEIGHT / cell_m), ncols=int(WIDTH / cell_m))
    xs, ys = grid.centers()
    xk, yk = (xs - X0) / 1000, (Y0 - ys) / 1000  # local km, y measured southwards

    # ridge along x = 20 km, lower towards the north (saddle near yk = 3)
    ridge_height = 60 + 260 / (1 + np.exp(-(yk - 7) / 1.5))
    elev = 50 + ridge_height * np.exp(-(((xk - 20) / 2.5) ** 2)) + 0.5 * xk

    lc = np.full(grid.shape, LandCover.OPEN, dtype=np.int_)
    forest = rasterize_polygons(grid, [_rect(16, 10, 24, 24)])
    lc[forest] = LandCover.FOREST
    town = (xk - 11) ** 2 + (yk - 12) ** 2 <= 1.4**2
    lc[town] = LandCover.URBAN
    wet = rasterize_polygons(grid, [_rect(27, 13, 31, 18)])
    lc[wet] = LandCover.WETLAND

    protected = rasterize_polygons(grid, [_rect(17, 13, 23, 24)])
    water = rasterize_polygons(grid, [_rect(34.0, 0, 34.4, 24)])
    corridor = rasterize_polygons(grid, [_rect(0, 2.9, 40, 3.1)])
    hazard = np.zeros(grid.shape, dtype=np.int_)
    hazard[np.abs(xk - 26 - 0.1 * yk) < 0.3] = 3  # fault zone
    rock = (np.abs(xk - 20) < 1.0) & (yk > 6)

    lines = {
        RIVER: [[_pt(5, 0), _pt(5.5, 12), _pt(4.5, 24)], [_pt(30, 0), _pt(29, 24)]],
        CrossingType.NATIONAL_HIGHWAY.value: [[_pt(0, 20), _pt(14, 9), _pt(40, 6)]],
        CrossingType.STATE_HIGHWAY.value: [[_pt(25, 0), _pt(25, 24)]],
        CrossingType.RAILWAY.value: [[_pt(8, 0), _pt(8, 24)]],
    }
    rng = np.random.default_rng(7)
    buildings: list[BuildingPoint] = []
    for _ in range(400):  # town: dense
        a, rr = rng.uniform(0, 2 * np.pi), 1.3 * np.sqrt(rng.uniform())
        x, y = _pt(11 + rr * np.cos(a), 12 + rr * np.sin(a))
        buildings.append(BuildingPoint(x, y, int(rng.integers(1, 4)), int(rng.integers(1, 3))))
    for vx, vy in ((14.5, 11.6), (30.5, 11.8), (36.5, 12.4)):  # villages near the straight line
        for _ in range(25):
            x, y = _pt(vx + rng.normal(0, 0.25), vy + rng.normal(0, 0.25))
            buildings.append(BuildingPoint(x, y))
    return AOI(
        grid=grid,
        elevation_m=elev,
        land_cover=lc,
        rock=rock,
        hazard=hazard,
        protected=protected,
        corridor=corridor,
        water=water,
        lines=lines,
        buildings=buildings,
    )
