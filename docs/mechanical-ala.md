# Mechanical Design: ALA Calculation Catalogue

**Methodology:** American Lifelines Alliance (ALA), *Guidelines for the Design of Buried Steel Pipe* (July 2001, with February 2005 addenda). Internal pressure design follows ASME B31.4 / B31.8 as adopted by OISD-STD-141 / OISD-STD-226 / PNGRB T4S. See [ADR 0005](adr/0005-ala-buried-pipe-methodology.md).

Code: `src/pipeline_analytics/mechanical/`. Every check returns a `CheckResult` (demand, capacity, utilisation, pass/fail, reference). `assess_segment()` runs all applicable checks for one route segment and reports the governing one.

## 1. Calculation catalogue

| # | ALA topic | Calculation | Function | Status |
|---|---|---|---|---|
| 1 | Internal pressure | Barlow hoop stress `S_h = P D / 2t` ≤ `F E T SMYS`; required wall incl. allowances; MAOP | `pressure.pressure_design` | ✅ |
| 1a | | Design factor F by location class (B31.8) / 0.72 (B31.4); temperature derating T | `pressure.design_factor`, `temperature_derating` | ✅ |
| 2 | Vertical earth load | Prism load `W = γ H D` | `vertical_loads.earth_load_n_per_m` | ✅ |
| 3 | Surface live load | Boussinesq point load with impact factor | `vertical_loads.boussinesq_pressure_pa` | ✅ |
| 4 | Ring deflection | Modified Iowa: `Δy = D_l K W r³ / (E I + 0.061 E' r³)`, check `Δy/D` | `vertical_loads.vertical_loads` | ✅ |
| 5 | Ring buckling | `q_a = (1/FS)(32 R_w B' E' E I / D³)^½` vs water + earth + live load + vacuum | `vertical_loads.vertical_loads` | ✅ |
| 6 | Thermal expansion | Restrained `S_L = ν S_h − E α ΔT`; unrestrained `S_L = S_h/2`; bending added | `thermal.thermal_stress` | ✅ |
| 6a | | Combined stress, von Mises or Tresca, ≤ 0.90 SMYS | `thermal.thermal_stress` | ✅ |
| 6b | | Virtual anchor length `L_a = |F| / T_u` | `thermal.thermal_stress` | ✅ |
| 7 | Soil springs: axial | `T_u = π D α c + π D H γ̄ (1+K₀)/2 tan(fφ)`; Δt = 3/5/8/10 mm | `soil.axial_spring` | ✅ |
| 8 | Soil springs: lateral | `P_u = N_ch c D + N_qh γ̄ H D`; Δp = 0.04(H + D/2) ≤ 0.10–0.15 D | `soil.lateral_spring` | ✅ |
| 9 | Soil springs: uplift | `Q_u = N_cv c D + N_qv γ̄ H D`; Δqu by soil type | `soil.uplift_spring` | ✅ |
| 10 | Soil springs: bearing | `Q_d = N_c c D + N_q γ̄ H D + N_γ γ D²/2`; Δqd = 0.1D / 0.2D | `soil.bearing_spring` | ✅ |
| 11 | Buoyancy | Empty pipe in water / swamp / liquefied soil, safety factor; required concrete weight coating thickness | `buoyancy.buoyancy` | ✅ |
| 12 | Seismic wave propagation | Ground strain `V/(α_ε C)`, curvature `A/(α_κ C²)`, soil-friction cap `T_u λ / 4AE` | `seismic.wave_propagation` | ✅ |
| 13 | PGD, longitudinal | Lateral spread or landslide block parallel to the pipe (elastic screening) | `seismic.longitudinal_pgd` | ✅ screening |
| 14 | Strain acceptance | Tensile limit; compressive `0.50 t/D' − 0.0025 + 3000(pD/2Et)²` (operability) or `1.76 t/D` (integrity) | `seismic.compressive_strain_limit`, `strain_checks` | ✅ |
| 15 | PGD, fault crossing | Pipe–soil FE model with the springs above; crossing angle optimisation | `fe.py`, `pgd.py` | ✅ Phase 2c (see `docs/mechanical-fe.md`) |
| 16 | PGD, transverse | Lateral spread or landslide perpendicular to the pipe | `pgd.py` | ✅ Phase 2c |
| 17 | PGD, settlement / subsidence | Vertical ground movement | `pgd.py` | ✅ Phase 2c |
| 18 | Road / rail crossings | API RP 1102 cased/uncased crossing stresses | — | Planned (crossing module) |
| 19 | Bends and above-ground transitions | Partially restrained thermal growth, bend stresses | — | Planned (Phase 2b) |

## 2. How the checks feed the feasibility study

For every route segment the optimiser will:

1. take the location class (from settlement density), soil, water table, crossings and geohazards (seismic zone, mapped faults, liquefaction and landslide susceptibility) from the GIS layers;
2. pick a candidate D / grade / wall from the PipeSpec Library and run `assess_segment()`;
3. resolve each failing check with the cheapest remedy, for example:

| Governing check | Typical remedy (and cost driver) |
|---|---|
| Hoop stress | thicker wall or higher grade (pipe tonnage) |
| Ring deflection / buckling | more wall, better bedding (higher E'), less cover, or a slab at crossings |
| Combined / longitudinal stress | lower design temperature, wall or grade, or anchor/bend design |
| Negative buoyancy | concrete weight coating, set-on weights or screw anchors |
| Wave propagation / PGD strain | heavier wall, low-friction coating, loose backfill, re-route around the hazard |

4. pass the resulting quantities (steel tonnage, concrete coating volume, special backfill) to the cost engine, so geohazards change CAPEX and therefore the route.

## 3. Values to verify before issuing a design

These defaults were compiled from widely published material. Confirm them against the licensed ALA document and codes, then record who verified them.

| Item | Current default | Where |
|---|---|---|
| India Class 1 Div 1 design factor | 0.80 not applied; 0.72 used | `pressure.design_factor` |
| B31.8 temperature derating table | 1.000 / 0.967 / 0.933 / 0.900 / 0.867 at 121–232 °C | `pressure._DERATING_*` |
| Code clause references (B31.4 403.x, B31.8 833.x, 841.x) | as cited in `CheckResult.ref` | all modules |
| Coating friction factors f | 1.0 / 0.9 / 0.8 / 0.7 / 0.6 / 0.6 | `soil.COATING_FRICTION_FACTOR` |
| Axial yield displacements | 3 / 5 / 8 / 10 mm | `soil._AXIAL_DISPLACEMENT_M` |
| N_qh coefficients (φ = 20–45°) and N_ch coefficients | ALA Table B-2 fit | `soil._NQH_COEF`, `soil.n_ch` |
| Lateral yield displacement cap | 0.15 D | `soil.lateral_spring(max_ratio=)` |
| Deflection lag D_l, bedding constant K | 1.5, 0.1 | `VerticalLoadInput` |
| Ring deflection limit | 3 % | `VerticalLoadInput.deflection_limit` |
| Buckling safety factor | 2.5 (H/D ≥ 2) / 3.0 | `vertical_loads` |
| Live-load impact factor | 1.5 | `boussinesq_pressure_pa` |
| Buoyancy safety factor | 1.1 | `BuoyancyInput.safety_factor` |
| Wave coefficients α_ε, α_κ; apparent velocity C | S: 2.0, 1.0; R: 1.0, 1.0; 2000 m/s | `seismic._WAVE_COEF` |
| Tensile strain limits | 2 % operability / 4 % pressure integrity | `StrainLimits` |
| Compressive strain limits | operability formula; 1.76 t/D integrity | `seismic.compressive_strain_limit` |
| Steel properties | E 207 GPa, ν 0.3, α 11.7e-6 /°C | `SteelProperties` |

All are parameters, so a project can override them without code changes.

## 4. Testing

`tests/mechanical/` checks every formula against an independent hand calculation (NPS 24 × 9.53 mm X70 at 92 barg in dense sand and soft clay) and checks expected physical behaviour. Examples: submerged soil gives lower friction; liquefied soil needs more concrete than water; Tresca ≥ von Mises; a hot line goes into compression.

Writing these tests caught one bug: in clay, N_c came out as zero because the ALA +0.001° offset had been applied only to the cotangent. It is now applied to the whole expression, so N_c → π + 2.
