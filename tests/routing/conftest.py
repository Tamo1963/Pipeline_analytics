import numpy as np
import pytest

from pipeline_analytics.core.jurisdiction import Service
from pipeline_analytics.routing import AOI, CostSurfaceParams, Grid, LandCover


def flat_aoi(nrows: int = 40, ncols: int = 60, cell: float = 100.0, **kw: object) -> AOI:
    g = Grid(x0=0.0, y0=nrows * cell, cell_m=cell, nrows=nrows, ncols=ncols)
    return AOI(
        grid=g,
        elevation_m=np.zeros(g.shape),
        land_cover=np.full(g.shape, LandCover.OPEN, dtype=np.int_),
        **kw,  # type: ignore[arg-type]
    )


@pytest.fixture
def gas_params() -> CostSurfaceParams:
    return CostSurfaceParams(service=Service.GAS, nps=24, design_pressure_pa_g=92e5)
