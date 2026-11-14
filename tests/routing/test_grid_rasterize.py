import math

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from scipy import ndimage

from pipeline_analytics.routing import Grid
from pipeline_analytics.routing.geometry import route_length
from pipeline_analytics.routing.rasterize import (
    density_per_km2,
    rasterize_lines,
    rasterize_polygons,
)

G = Grid(x0=1000.0, y0=5000.0, cell_m=100.0, nrows=40, ncols=60)


def test_grid_round_trip() -> None:
    x, y = G.center(3, 7)
    assert (x, y) == (1750.0, 4650.0)
    assert G.rc(x, y) == (3, 7)
    with pytest.raises(ValueError):
        G.rc(0, 0)
    with pytest.raises(ValueError):
        Grid(x0=0, y0=0, cell_m=0, nrows=5, ncols=5)


@settings(max_examples=60, deadline=None)
@given(st.floats(1050, 6950), st.floats(1050, 4950), st.floats(1050, 6950), st.floats(1050, 4950))
def test_supercover_line_is_4_connected(x1: float, y1: float, x2: float, y2: float) -> None:
    """A rasterised segment forms one 4-connected component (no diagonal gaps)."""
    if math.dist((x1, y1), (x2, y2)) < 1:
        return
    m = rasterize_lines(G, [[(x1, y1), (x2, y2)]])
    _, n = ndimage.label(m)  # default structure = 4-connectivity
    assert n == 1
    assert m[G.rc(x1, y1)] and m[G.rc(x2, y2)]


def test_polygon_area() -> None:
    m = rasterize_polygons(G, [[(1000, 5000), (3000, 5000), (3000, 4000), (1000, 4000)]])
    assert m.sum() == 20 * 10
    tri = rasterize_polygons(G, [[(1000, 5000), (5000, 5000), (1000, 1000)]])
    assert tri.sum() == pytest.approx(0.5 * 40 * 40, rel=0.05)


def test_density() -> None:
    d = density_per_km2(G, [(3050.0, 3050.0, 10.0)], radius_m=450)
    r, c = G.rc(3050, 3050)
    n_cells = (d > 0).sum()
    assert d[r, c] == pytest.approx(10 / (n_cells * 0.01))
    assert d[r, c + 10] == 0
    assert np.allclose(d[d > 0], d[r, c])  # uniform within the radius


def test_route_length() -> None:
    assert route_length([(0, 0), (3, 4), (3, 10)]) == 11
