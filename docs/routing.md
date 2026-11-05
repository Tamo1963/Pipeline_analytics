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
| `geohazards` | fault traces and ground-movement zones (section 6) | burnt into `hazard`; pipe strain checked on every route |
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

## 6. Geohazards: faults and ground-movement zones (`routing/geohazards.py`)

Mapped hazards are vector layers with design movements. They come from GeoJSON (`kind` = `fault`, `landslide`, `lateral_spread` or `subsidence`) or are built in code:

| Layer | Geometry | Properties |
|---|---|---|
| `Fault` | trace (polyline) | `slip_m`, `dip_deg`, `rake_deg` (0 left-lateral, 180 right-lateral, 90 reverse, −90 normal), `zone_half_width_m` (hazard band). Digitise the trace so the fault **dips to the right** (Aki–Richards strike). |
| `GroundZone` | polygon | `kind`, `displacement_m`, `direction_azimuth_deg` (landslide / lateral spread, clockwise from north), `pattern` (cosine or block) |

How they link into routing:

1. **Cost surface.** Fault bands and zones are burnt into `hazard` (maximum class), so the router avoids them as before.
2. **Crossings.** `extract_route` lists every crossing (`RouteExtraction.geohazard_crossings`):
   * for a fault: chainage, the crossing angle (from the pipe direction to the strike), and the slip resolved into axial, transverse and vertical components for the block ahead;
   * for a zone: the length of pipe inside it and the movement in pipe axes.
3. **Strain check.** `assess_geohazards` runs the pipe–soil FE model (`docs/mechanical-fe.md`) at each crossing, using the design's wall for the location class there. If a strain limit is exceeded, it prices the lightest standard wall that passes over the affected length (zone + 2 × max(100 m, 50 D)), with indirects. For faults it also reports the best crossing angle from a 30–150° sweep.
4. **Realignment.** `realign_fault_crossing` routes to a waypoint 1.5 km before the fault, crosses on a straight leg at the chosen angle, and routes on from a waypoint 1.5 km after it. The leg is straight because the hazard band otherwise pulls the router back to a perpendicular crossing.
5. **Optimiser.** `optimise_route` checks every corridor's best design:
   * The present value of the mitigation cost is added to the corridor's lifecycle cost.
   * Corridors with a crossing that no standard wall can make safe rank last.
   * A failing fault crossing also spawns a realigned variant (`C1-realigned 30 deg`), which is evaluated in full. Heavier wall in place and realignment then compete on lifecycle cost.

Fixed (leg-by-leg) routes take the same hazards per leg (`Leg.geohazards`: kind, position, displacement, angle). The bot's `define_route` accepts them, and `run_design_search` / `evaluate_design` return the strain checks and mitigation for the design.

**Sample area.** It now carries the fault trace inside its fault band (2 m right-lateral, crossed at about 84°: the pipe is stretched, strain utilisation 0.74 for 24" X70, passes) and a landslide on the ridge's eastern flank. With left-lateral slip the same crossing is compressed and fails at 1 m. Two remedies are then available:

* a 12.7 mm wall over 200 m (about ₹0.09 crore);
* a realigned crossing at 30°: strain utilisation 0.36, but 2.8 km longer.

The optimiser weighs the two on lifecycle cost.
