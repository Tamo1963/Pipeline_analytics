# Class Location and Hydraulics

Phase 2b of the plan. Code: `src/pipeline_analytics/codes/class_location.py`, `src/pipeline_analytics/hydraulics/`, `src/pipeline_analytics/routing/geometry.py`.

## 1. Location class (gas pipelines)

ASME B31.8 §840.2, as adopted by PNGRB T4S and OISD-STD-226. Liquid pipelines (B31.4 / OISD-STD-141) have no location classes.

| Rule | Implementation |
|---|---|
| Class location unit: 1.6 km (1 mile) × 200 m (220 yd) each side of the centreline, sliding | every window start that can change the count (grid + building chainages) |
| Count buildings for human occupancy; each dwelling unit counts | `Building.dwelling_units` |
| Class 1 ≤ 10, Class 2 11–45, Class 3 ≥ 46 | `_unit_class` |
| Class 3 within 91 m of a place occupied by ≥ 20 people | `AssemblyPlace` |
| Class 4 where 4+ storey buildings are prevalent; ends 200 m from the nearest | any 4+ storey building raises ±200 m (judgement: see below) |
| Cluster rule: Class 2/3 may end 200 m from the nearest building of the cluster | `cluster_rule=True` (default); `False` raises the whole 1.6 km unit |
| Each point takes the highest class of any unit covering it | |

Output: merged `ClassSegment`s (from/to chainage, class, reason), `class_summary()` length per class, and `maop_along_route()`, which gives the MAOP of a uniform wall and grade at each chainage. That MAOP becomes the hydraulic envelope, and it shows where a heavier wall is needed.

Buildings are given by chainage and offset. `routing.geometry.chainage_offset()` projects x/y points (projected metres) onto the centreline. Extracting buildings from GIS data (OSM / Open Buildings) comes with the routing engine (Phase 4).

Evaluated on a 10 m grid by default (`step_m`), so boundaries are accurate to the grid step.

## 2. Hydraulics

### Gas

Steady, isothermal flow, kinetic energy neglected. Per profile segment:

```
P1² − e^s P2² = f ṁ² Z R T L_e / (D A² M)
s = 2 g M Δh / (Z R T),   L_e = L (e^s − 1) / s
```

* Derived from first principles; the tests check it against an RK4 integration of `dP/dx` (flat, uphill and downhill agree within 0.01 %). It is the General Flow Equation in mass-flow form. Panhandle A/B and Weymouth are empirical forms of its friction factor.
* f: Colebrook (Darcy) divided by E² for a pipeline efficiency E (default 1.0).
* Z: Papay with Standing pseudo-critical properties from specific gravity, evaluated at the segment average pressure `(2/3)(P1 + P2 − P1P2/(P1+P2))`. Use AGA-8 / GERG-2008 with a composition for detailed design (planned).
* Flow in standard m³ (ISO 13443, 15 °C, 101.325 kPa). Accepts `Q_(6, "MMSCMD")`.
* Compressor power: `ṁ Z₁ R T₁/M · k/(k−1) · [(P₂/P₁)^((k−1)/k) − 1] / η`; fuel gas = power / (driver efficiency × LHV).
* Checks: MAOP envelope, delivery pressure, velocity ≤ min(design limit, API RP 14E erosional velocity `122/√ρ`), compression ratio.

### Liquid

`P2 = P1 − ρ g Δz − f (L/D) ρ v²/2` with Colebrook / laminar f. Flow from m³/s, bpd or MMTPA (with density).

* Minimum line pressure = vapour pressure + margin (default 1 bar), so the line stays packed over peaks (no slack-line flow).
* Pump power = Q ΔP / (η_pump η_motor).
* Checks: MAOP envelope (static head gain on downhill runs is caught), minimum line pressure, delivery pressure, velocity.

### Station siting (both)

1. Greedy march: each station discharges at its cap (the lowest of discharge limit, MAOP at the station, and max ratio × suction). A station is placed at the last point with at least the minimum suction pressure before the line pressure or delivery pressure would be violated.
2. Discharge trimming: a backward pass finds the lowest discharge each leg needs, and from that the lowest suction its station can accept. The compression-ratio limit couples the two; a forward-only trim broke this, the tests caught it, and it is fixed. A forward pass then sets actual suctions and discharges, so power is realistic, and a station that needs no boost is dropped.
3. Stations sit on profile points. Use `Profile.resample(1000)` for 1 km resolution; original vertices such as peaks are kept.

This gives a feasible, sensible station layout for screening. Choosing station count, spacing and discharge for minimum lifecycle cost is the optimiser's job (Phase 5).

## 3. Values to verify / defaults

| Item | Default | Where |
|---|---|---|
| Class location definitions and boundary distances in current PNGRB T4S / OISD-STD-226 | B31.8 840.2 values | `class_location` constants |
| "Prevalent" 4+ storey buildings | any one such building | `CLASS4_MIN_STOREYS` logic |
| Pipe roughness | 45.7 µm (bare commercial steel); internally coated ~5–10 µm | `DEFAULT_ROUGHNESS_M` |
| Gas: viscosity, k, flowing temperature, LHV | 1.1e-5 Pa·s, 1.30, 25 °C, 47 MJ/kg | `Gas` |
| Compressor η / driver η / max ratio / min suction | 0.80 / 0.33 / 1.6 / 40 barg | `GasStationInput` |
| Gas velocity limit; erosional constant | 20 m/s; C = 100 (US) = 122 (SI) | `GasStationInput`, `API_14E_C_SI` |
| Pump η / motor η / min suction / slack margin | 0.75 / 0.95 / 3 barg / 1 bar | `PumpStationInput` |
| Liquid velocity limit | 3 m/s | `PumpStationInput` |

## 4. Not yet included

* Gas temperature profile (heat exchange with ground, Joule–Thomson); isothermal for now.
* Transients and surge; line pack; pigging.
* Drag-reducing agent; batch (multi-product) operation; viscosity–temperature profile for waxy crude.
* AGA-8 / GERG-2008 compressibility from composition.
