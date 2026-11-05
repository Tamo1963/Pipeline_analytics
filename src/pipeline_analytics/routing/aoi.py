"""Area of interest: the raster and vector layers the router works on."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
from numpy.typing import NDArray

from pipeline_analytics.codes.class_location import Building
from pipeline_analytics.cost.rates import CrossingType, LandUse, Terrain
from pipeline_analytics.routing.grid import Grid
from pipeline_analytics.routing.rasterize import XY, rasterize_lines, rasterize_polygons

if TYPE_CHECKING:
    from pipeline_analytics.routing.geohazards import Geohazards

ZONE_KINDS = ("landslide", "lateral_spread", "subsidence")


class LandCover(IntEnum):
    """Land-cover codes for the ``land_cover`` raster (map ESA WorldCover etc. to these)."""

    OPEN = 0  # cropland, grassland, shrubland
    FOREST = 1
    URBAN = 2
    BARREN = 3
    WATER = 4
    WETLAND = 5


LAND_USE_OF = {
    LandCover.OPEN: LandUse.AGRICULTURAL,
    LandCover.FOREST: LandUse.FOREST,
    LandCover.URBAN: LandUse.URBAN,
    LandCover.BARREN: LandUse.WASTELAND,
    LandCover.WATER: LandUse.WASTELAND,
    LandCover.WETLAND: LandUse.WASTELAND,
}
TERRAIN_OF = {
    LandCover.OPEN: Terrain.FLAT,
    LandCover.FOREST: Terrain.FOREST,
    LandCover.URBAN: Terrain.URBAN,
    LandCover.BARREN: Terrain.FLAT,
    LandCover.WATER: Terrain.MARSH,
    LandCover.WETLAND: Terrain.MARSH,
}

# linear feature kinds: every CrossingType plus "river" (narrow river / stream)
RIVER = "river"
LINEAR_KINDS: tuple[str, ...] = (RIVER, *(c.value for c in CrossingType))


@dataclass(frozen=True)
class BuildingPoint:
    x: float
    y: float
    dwelling_units: int = 1
    storeys: int = 1


@dataclass
class AOI:
    """All layers on one grid. Rasters are (nrows, ncols) arrays."""

    grid: Grid
    elevation_m: NDArray[np.float64]
    land_cover: NDArray[np.int_]
    rock: NDArray[np.bool_] | None = None
    hazard: NDArray[np.int_] | None = None
    """0 none, 1 low, 2 medium, 3 high (faults, landslide, liquefaction)."""
    protected: NDArray[np.bool_] | None = None
    """Wildlife sanctuaries, national parks, ASI sites: excluded or penalised."""
    corridor: NDArray[np.bool_] | None = None
    """Existing ROW (pipelines, power lines, highways) where sharing is allowed."""
    water: NDArray[np.bool_] | None = None
    """Wide water bodies (rivers as polygons, reservoirs): crossed by HDD."""
    lines: dict[str, list[list[XY]]] = field(default_factory=dict)
    buildings: list[BuildingPoint] = field(default_factory=list)
    geohazards: Geohazards | None = None
    """Mapped faults and ground-movement zones. They are burnt into ``hazard`` (max
    class) and checked for strain on every route (see routing.geohazards)."""

    def __post_init__(self) -> None:
        shape = self.grid.shape
        for name in (
            "elevation_m",
            "land_cover",
            "rock",
            "hazard",
            "protected",
            "corridor",
            "water",
        ):
            arr = getattr(self, name)
            if arr is not None and arr.shape != shape:
                raise ValueError(f"{name} has shape {arr.shape}, grid is {shape}")
        unknown = set(self.lines) - set(LINEAR_KINDS)
        if unknown:
            raise ValueError(f"unknown linear feature kinds: {sorted(unknown)}")
        self._line_masks: dict[str, NDArray[np.bool_]] = {}
        if self.geohazards:
            burnt = self.geohazards.hazard_raster(self.grid)
            self.hazard = burnt if self.hazard is None else np.maximum(self.hazard, burnt)

    def layer(self, name: str) -> NDArray[np.bool_]:
        arr = getattr(self, name)
        return arr if arr is not None else self.grid.empty(bool)

    def line_mask(self, kind: str) -> NDArray[np.bool_]:
        if kind not in self._line_masks:
            self._line_masks[kind] = rasterize_lines(self.grid, self.lines.get(kind, []))
        return self._line_masks[kind]

    def slope_pct(self) -> NDArray[np.float64]:
        gy, gx = np.gradient(self.elevation_m, self.grid.cell_m)
        return np.asarray(100 * np.hypot(gx, gy), dtype=np.float64)

    def buildings_as(self) -> list[tuple[float, float, float]]:
        return [(b.x, b.y, float(b.dwelling_units)) for b in self.buildings]


def to_buildings(
    points: list[BuildingPoint], chainage_offsets: list[tuple[float, float]]
) -> list[Building]:
    return [
        Building(chainage_m=ch, offset_m=off, dwelling_units=b.dwelling_units, storeys=b.storeys)
        for b, (ch, off) in zip(points, chainage_offsets, strict=True)
    ]


def load_geojson_features(path: str | Path) -> dict[str, Any]:
    """Read a GeoJSON FeatureCollection (projected metres) into AOI inputs.

    Each feature needs a ``kind`` property:
    * LineString / MultiLineString: one of ``LINEAR_KINDS`` (river, national_highway, ...)
    * Polygon / MultiPolygon: ``water``, ``protected`` or ``corridor``
    * Point: ``building``, with optional ``dwelling_units`` and ``storeys``
    * LineString ``fault`` with ``name``, ``slip_m`` and optional ``dip_deg``,
      ``rake_deg``, ``zone_half_width_m`` (digitised with the fault dipping to the right)
    * Polygon ``landslide`` / ``lateral_spread`` / ``subsidence`` with ``name``,
      ``displacement_m`` and (not for subsidence) ``direction_azimuth_deg``

    Returns ``{"lines": {kind: [...]}, "polygons": {layer: [...]}, "buildings": [...],
    "geohazards": Geohazards}``.
    """
    from pipeline_analytics.routing.geohazards import Fault, Geohazards, GroundZone

    faults: list[Fault] = []
    zones: list[GroundZone] = []
    skip = {"kind", "name"}
    data = json.loads(Path(path).read_text())
    lines: dict[str, list[list[XY]]] = {}
    polys: dict[str, list[list[XY]]] = {}
    buildings: list[BuildingPoint] = []
    for f in data.get("features", []):
        geom, props = f["geometry"], f.get("properties") or {}
        kind = str(props.get("kind", ""))
        t, coords = geom["type"], geom["coordinates"]
        name = str(props.get("name", f"{kind} {len(faults) + len(zones) + 1}"))
        extra = {k: v for k, v in props.items() if k not in skip}
        if kind == "fault" and t in ("LineString", "MultiLineString"):
            for part in [coords] if t == "LineString" else coords:
                faults.append(Fault(name=name, trace=tuple((p[0], p[1]) for p in part), **extra))
            continue
        if kind in ZONE_KINDS and t in ("Polygon", "MultiPolygon"):
            for ring in [coords[0]] if t == "Polygon" else [p[0] for p in coords]:
                poly = tuple((p[0], p[1]) for p in ring)
                zones.append(GroundZone(name=name, kind=kind, polygon=poly, **extra))
            continue
        if t in ("LineString", "MultiLineString"):
            parts = [coords] if t == "LineString" else coords
            lines.setdefault(kind, []).extend([[(p[0], p[1]) for p in part] for part in parts])
        elif t in ("Polygon", "MultiPolygon"):
            rings = [coords[0]] if t == "Polygon" else [p[0] for p in coords]
            polys.setdefault(kind, []).extend([[(p[0], p[1]) for p in ring] for ring in rings])
        elif t == "Point" and kind == "building":
            buildings.append(
                BuildingPoint(
                    coords[0],
                    coords[1],
                    int(props.get("dwelling_units", 1)),
                    int(props.get("storeys", 1)),
                )
            )
    return {
        "lines": lines,
        "polygons": polys,
        "buildings": buildings,
        "geohazards": Geohazards(faults=tuple(faults), zones=tuple(zones)),
    }


def aoi_from_layers(
    grid: Grid,
    elevation_m: NDArray[np.float64],
    land_cover: NDArray[np.int_],
    features: dict[str, Any] | None = None,
    **rasters: NDArray,  # type: ignore[type-arg]
) -> AOI:
    """Build an AOI from rasters plus the output of :func:`load_geojson_features`."""
    features = features or {"lines": {}, "polygons": {}, "buildings": []}
    layers = dict(rasters)
    for name, rings in features["polygons"].items():
        if name not in ("water", "protected", "corridor"):
            raise ValueError(f"unknown polygon layer {name!r}")
        mask = rasterize_polygons(grid, rings)
        layers[name] = mask | layers[name] if name in layers else mask
    return AOI(
        grid=grid,
        elevation_m=elevation_m,
        land_cover=land_cover,
        lines=features["lines"],
        buildings=features["buildings"],
        geohazards=features.get("geohazards") or None,
        **layers,
    )
