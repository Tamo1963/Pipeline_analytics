"""Cost surface: installed cost per metre of pipeline in every cell (INR/m).

Cell cost = indirect factor x [ pipe + coating + systems
                               + construction(terrain) x hazard factor x corridor factor
                               + land(land use) x corridor factor ]

* Pipe: tonnes per metre for the wall a location class needs. The class is
  estimated from building density (~17 units/km^2 for Class 2, ~72 for Class 3,
  matching an 11 / 46 dwelling 1.6 km x 400 m unit); the exact classes are
  computed for the final alignment.
* Construction: laying rate per inch-km x terrain factor. Terrain comes from
  land cover, slope (rolling > 5 %, hilly > 15 %) and rock.
* Wide water cells (``AOI.water``) cost HDD per metre instead of laying + land.
* Linear features (rivers, roads, railways, canals, pipelines) add a lump
  *entry cost* each time the path crosses them (see pathfinding).
* Protected areas and slopes over ``max_slope_pct`` are excluded (inf), or
  penalised by ``protected_factor``.
* Indirect factor: (1 + taxes + engineering + owner's cost) x (1 + contingency),
  so path costs are comparable with the Phase 3 CAPEX.

The OPEX-equivalent ``length_per_m`` penalty (friction energy + line O&M, PV)
is applied on graph edges; see ``routing.penalties`` for why elevation is not a
routing penalty by default.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

import numpy as np
from numpy.typing import NDArray
from pydantic import Field

from pipeline_analytics.core.jurisdiction import Service
from pipeline_analytics.cost.rates import (
    CrossingType,
    LandUse,
    Terrain,
    UnitRates,
    _Model,
    placeholder_rates,
)
from pipeline_analytics.mechanical.common import PipeSection
from pipeline_analytics.mechanical.pressure import LocationClass, pressure_design
from pipeline_analytics.pipespec import default_library
from pipeline_analytics.routing.aoi import (
    AOI,
    LAND_USE_OF,
    LINEAR_KINDS,
    RIVER,
    TERRAIN_OF,
    LandCover,
)
from pipeline_analytics.routing.rasterize import density_per_km2

TERRAINS = list(Terrain)
LAND_USES = list(LandUse)
CLASS2_DENSITY_KM2 = 11 / 0.64
CLASS3_DENSITY_KM2 = 46 / 0.64


class CostSurfaceParams(_Model):
    service: Service
    nps: str | float
    grade: str = "X70"
    psl: int = 2
    design_pressure_pa_g: float = Field(gt=0)
    corrosion_allowance_m: float = Field(default=0.0, ge=0)
    max_d_over_t: float = 96.0
    coating: str = "3LPE"
    rates: UnitRates = Field(default_factory=placeholder_rates)
    rolling_slope_pct: float = 5.0
    hilly_slope_pct: float = 15.0
    max_slope_pct: float = 60.0
    protected_policy: Literal["exclude", "penalty"] = "exclude"
    protected_factor: float = Field(default=5.0, ge=1)
    hazard_factor: tuple[float, float, float, float] = (1.0, 1.1, 1.3, 2.0)
    corridor_factor: float = Field(default=0.85, gt=0, le=1)
    narrow_river_width_m: float = Field(default=30.0, gt=0)
    climb_per_m: float = Field(
        default=0.0, ge=0, description="heuristic only: INR per m of gross rise (see penalties)"
    )
    descent_per_m: float = Field(default=0.0, ge=0)
    length_per_m: float = Field(default=0.0, ge=0, description="INR per m of length (OPEX PV)")


@dataclass
class CostSurface:
    aoi: AOI
    params: CostSurfaceParams
    cell_cost: NDArray[np.float64]
    """INR per metre of pipeline through the cell (inf = excluded)."""
    terrain: NDArray[np.int_]
    """Index into ``TERRAINS``."""
    land_use: NDArray[np.int_]
    """Index into ``LAND_USES``."""
    class_proxy: NDArray[np.int_]
    """1, 2 or 3 from building density (gas only; 1 for liquids)."""
    entry_cost: dict[str, float]
    """INR per crossing of each linear feature kind."""
    pipe_cost_per_m: dict[int, float]
    od_m: float
    indirect_factor: float

    @property
    def grid(self):  # type: ignore[no-untyped-def]
        return self.aoi.grid


def wall_for_class(p: CostSurfaceParams, od_m: float, smys_pa: float, lc: LocationClass) -> float:
    """Pressure wall + D/t limit, rounded up to 0.1 mm (same rule as option evaluation)."""
    probe = PipeSection(od_m=od_m, wt_m=od_m / 10, smys_pa=smys_pa)
    req = pressure_design(
        probe, p.design_pressure_pa_g, p.service, lc, allowance=p.corrosion_allowance_m
    ).required_wt_m
    req = max(req, od_m / p.max_d_over_t)
    return math.ceil(round(req / 1e-4, 9)) * 1e-4


def terrain_index(aoi: AOI, p: CostSurfaceParams) -> NDArray[np.int_]:
    """Terrain class per cell.

    Precedence: rock > urban > marsh > hilly > rolling > forest > flat.
    """
    lc = aoi.land_cover
    out = np.full(aoi.grid.shape, TERRAINS.index(Terrain.FLAT), dtype=np.int_)
    for code, terr in TERRAIN_OF.items():
        if terr is Terrain.FOREST:
            out[lc == code] = TERRAINS.index(terr)
    slope = aoi.slope_pct()
    out[slope > p.rolling_slope_pct] = TERRAINS.index(Terrain.ROLLING)
    out[slope > p.hilly_slope_pct] = TERRAINS.index(Terrain.HILLY)
    for code, terr in TERRAIN_OF.items():
        if terr in (Terrain.MARSH, Terrain.URBAN):
            out[lc == code] = TERRAINS.index(terr)
    if aoi.rock is not None:
        out[aoi.rock] = TERRAINS.index(Terrain.ROCK)
    return out


def land_use_index(aoi: AOI) -> NDArray[np.int_]:
    out = np.zeros(aoi.grid.shape, dtype=np.int_)
    for code, lu in LAND_USE_OF.items():
        out[aoi.land_cover == code] = LAND_USES.index(lu)
    return out


def build_cost_surface(aoi: AOI, p: CostSurfaceParams) -> CostSurface:
    lib = default_library()
    size = lib.size(p.nps)
    grade = lib.grade(p.grade, psl=p.psl)
    od = size.od_mm / 1000
    od_in = od / 0.0254
    r = p.rates

    # pipe + coating per metre, by (proxy) location class
    classes = (
        [LocationClass.CLASS_1_DIV_2, LocationClass.CLASS_2, LocationClass.CLASS_3]
        if p.service is Service.GAS
        else [LocationClass.CLASS_1_DIV_2]
    )
    coat = math.pi * od * r.coating_per_m2[p.coating]
    pipe_cost: dict[int, float] = {}
    for i, lc in enumerate(classes, start=1):
        wt = wall_for_class(p, od, grade.smys * 1e6, lc)
        procs = [q for q in lib.processes_for(size.od_mm, wt * 1000, psl=p.psl)
                 if q.code in r.line_pipe.process_factor]  # fmt: skip
        if not procs:
            raise ValueError(f"no process makes OD {size.od_mm} mm x {wt * 1000:.1f} mm")
        proc = min(procs, key=lambda q: r.line_pipe.process_factor[q.code])
        tonnes_per_m = math.pi * 7850.0 * (od - wt) * wt / 1000
        pipe_cost[i] = tonnes_per_m * r.pipe_rate_per_tonne(grade.name, proc.code) + coat

    density = density_per_km2(aoi.grid, aoi.buildings_as())
    cls = np.ones(aoi.grid.shape, dtype=np.int_)
    if p.service is Service.GAS:
        cls[density >= CLASS2_DENSITY_KM2] = 2
        cls[density >= CLASS3_DENSITY_KM2] = 3
    pipe = np.vectorize(pipe_cost.__getitem__, otypes=[float])(cls)

    terr = terrain_index(aoi, p)
    tf = np.array([r.terrain_factor[t] for t in TERRAINS])[terr]
    construction = r.laying_per_inch_km / 1000 * od_in * tf
    lu = land_use_index(aoi)
    land = np.array([r.land.rou_compensation_per_ha[u] for u in LAND_USES])[lu]
    land = land * r.land.row_width_m / 10_000

    haz = np.zeros(aoi.grid.shape, dtype=np.int_) if aoi.hazard is None else aoi.hazard
    construction = construction * np.asarray(p.hazard_factor)[np.clip(haz, 0, 3)]
    corr = aoi.layer("corridor")
    construction = np.where(corr, construction * p.corridor_factor, construction)
    land = np.where(corr, land * p.corridor_factor, land)

    water = aoi.layer("water") | (aoi.land_cover == LandCover.WATER)
    hdd = r.crossings.hdd_per_inch_m * od_in
    construction = np.where(water, hdd, construction)
    land = np.where(water, 0.0, land)

    systems = (r.per_km.cathodic_protection + r.per_km.scada_telecom) / 1000
    ind = r.indirects
    factor = (1 + ind.taxes + ind.engineering_pmc + ind.owners_cost) * (1 + ind.contingency)
    cost = factor * (pipe + construction + land + systems)

    prot = aoi.layer("protected")
    if p.protected_policy == "exclude":
        cost = np.where(prot, np.inf, cost)
    else:
        cost = np.where(prot, cost * p.protected_factor, cost)
    cost = np.where(aoi.slope_pct() > p.max_slope_pct, np.inf, cost)

    entry = {
        RIVER: factor * r.crossings.open_cut_river_per_inch_m * od_in * p.narrow_river_width_m,
        **{ct.value: factor * r.crossings.per_crossing[ct] for ct in CrossingType},
    }
    assert set(entry) == set(LINEAR_KINDS)
    return CostSurface(
        aoi=aoi,
        params=p,
        cell_cost=cost,
        terrain=terr,
        land_use=lu,
        class_proxy=cls,
        entry_cost=entry,
        pipe_cost_per_m=pipe_cost,
        od_m=od,
        indirect_factor=factor,
    )
