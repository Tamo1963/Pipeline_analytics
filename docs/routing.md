# Route / Alignment Engine

Phase 4 of the plan. Code: `src/pipeline_analytics/routing/`. Decision record: [ADR 0007](adr/0007-router-objective-and-elevation.md).

```
AOI layers ──► cost surface (INR/m) ──► least-cost path / alternatives ──► extraction ──► OptionInput ──► LCC
 (DEM, land cover,   same unit rates      Dijkstra, 8-neighbour,      profile, terrain,      Phase 3
  features, buildings) as Phase 3          crossing entry costs        crossings, classes     evaluation
```

## 1. Area of interest (AOI)

All layers sit on one `Grid` in projected metres (e.g. UTM 43N, EPSG:32643).

| Layer | Type | Used for |
|---|---|---|
| `elevation_m` | raster (DEM, e.g. Copernicus GLO-30) | slope → terrain class, profile |
| `land_cover` | raster of `LandCover` codes (map ESA WorldCover etc.) | terrain, land use (ROU cost) |
| `rock` | bool raster | rock terrain |
| `hazard` | 0–3 raster (faults, landslide, liquefaction) | construction factor ×1.0/1.1/1.3/2.0 |
| `protected` | bool (sanctuaries, national parks, ASI sites) | excluded (or ×5 penalty) |
| `corridor` | bool (existing ROW that can be shared) | land + construction ×0.85 |
| `water` | bool (wide rivers, reservoirs) | HDD cost per metre, no land cost |
| `lines` | river, national/state highway, minor road, railway, canal, foreign pipeline | lump crossing cost per entry |
| `buildings` | points with dwelling units and storeys | class-location proxy (density) and exact classes after routing |

Real data: rasters as numpy arrays (read GeoTIFFs with rasterio or GDAL), vectors via `load_geojson_features()` (LineStrings with `kind`, Polygons with `water`/`protected`/`corridor`, building Points), combined with `aoi_from_layers()`. Downloading OSM, DEM and WDPA data is a data-preparation step outside the package for now.

## 2. Cost surface (INR per metre)

```
cell cost = indirects × [ pipe(class proxy) + coating + systems
                        + laying × terrain factor × hazard factor × corridor factor
                        + ROU land × corridor factor ]
```

* Pipe cost uses the wall the location class needs. The class is estimated from building density within 450 m (~17 units/km² for Class 2, ~72 for Class 3, i.e. 11 / 46 dwellings per 1.6 km × 400 m unit). The exact classes are computed on the final alignment.
* Terrain: rock > urban > marsh > hilly (> 15 % slope) > rolling (> 5 %) > forest > flat. Slopes over 60 % are excluded.
* Water cells cost HDD per metre and no land.
* Indirects use the same factor as the CAPEX estimate: (1 + taxes + engineering + owner's) × (1 + contingency).
* Linear features add a lump entry cost (× indirects) each time the path crosses them; narrow rivers are open-cut at a 30 m nominal width.
* `length_per_m`: the OPEX-equivalent penalty (PV of friction energy + line O&M per metre) from `opex_penalties()`.

## 3. Search

* Directed 8-neighbour graph; scipy `csgraph.dijkstra` (a 400 × 240 grid solves in well under a second).
* Linear features are rasterised **4-connected (supercover)**, so an 8-connected path cannot cross a road diagonally without entering it. Every crossing is paid, and counted, exactly once. A property test checks this at random angles.
* Waypoints are supported.
* **Alternatives:** penalty method. After each corridor, cells within the buffer get ×2 dearer. A corridor is kept only if ≤ 50 % of it lies within the buffer of earlier ones. The default buffer is 7.5 % of the straight-line distance, which gives genuinely different routes rather than parallel lanes. All corridors are re-costed on the original surface.
* 8-neighbour paths are up to ~8 % longer than straight lines in some directions. Corridors are smoothed (Douglas–Peucker, never through excluded cells) before extraction.

## 4. Extraction (any alignment)

`extract_route(surface, polyline)` samples every 1/8 cell and returns:

* length, profile (every 1 km and at vertices);
* length by terrain and by land use (HDD lengths excluded from both);
* crossings by feature kind, HDD length, wetland length (→ concrete coating), protected length, length by hazard class, shared-corridor length;
* buildings converted to chainage/offset, then **exact location classes** (B31.8 840.2);
* `RouteQuantities` for the BOQ, and `.option_input(base)`, which returns the Phase 3 `OptionInput` for this alignment.

It works equally for router output and for an alignment you supply (e.g. digitised from a survey).

## 5. Export

`to_geojson()` writes projected coordinates with a `crs` member. `to_kml()` writes WGS84 for Google Earth and needs the `gis` extra (pyproj).

## 6. Acceptance (plan Phase 4), on the synthetic sample area

`routing/sample.py` is a synthetic 40 × 24 km area: a ridge with a northern saddle, a protected forest, a 400 m river, highways, a railway, a town, villages, a wetland, a fault zone and a power-line corridor.

* ≥ 3 distinct corridors in well under 2 minutes (≈ 0.3 s).
* Crossings match a manual count of the layout: 2 streams, 1 national highway (an odd number for detours), 1 state highway, 1 railway, ~400 m of HDD. Every corridor crosses the fault zone and none enters the protected forest.
* The best corridor avoids the town (Class 1 throughout).
* Every corridor evaluates end to end (lifecycle cost from Phase 3).

## 7. Not yet included

* Data download/preparation from OSM, Copernicus DEM, ESA WorldCover and WDPA.
* 16-neighbour search (smoother, shorter grid paths).
* Iterating route ↔ hydraulics ↔ diameter to convergence (Phase 5 optimiser).
* Crossing-angle rules (e.g. near-perpendicular road/rail crossings), bend-radius constraints, and minimum distances from buildings (OISD/T4S) as hard constraints.
