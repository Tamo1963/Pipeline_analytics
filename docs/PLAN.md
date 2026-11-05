# Pipeline Analytics Suite: Master Plan

**Scope:** A suite of applications for **cross-country (onshore transmission) pipelines**. The first two parts are:

1. **PipeSpec Library**: a structured, queryable library of line-pipe specifications used in the pipeline industry.
2. **Feasibility Specialist Bot**: a conversational agent that runs viability studies. It iterates on pipeline alignment, diameter, wall thickness, grade and station configuration to **minimise the present value of CAPEX + OPEX over the concession period**.

The other suite apps (detailed design, CP design, HDD design, construction planning, integrity management) are built later on the same core (see §9).

---

## 1. Guiding principles

| Principle | What it means in practice |
|---|---|
| **Engine first, bot second** | All engineering and economics lives in a tested, deterministic Python core. The bot (LLM) only plans, asks questions, calls tools and explains results. It never "computes" numbers itself. |
| **Traceability** | Every number in a report traces back to an input, a library record (with standard and clause reference) or a cost-database entry, with versions. |
| **Iteration is first-class** | A study is a tree of *scenarios*. Each iteration (new corridor, new diameter, new weights) becomes a new scenario that can be compared side by side. |
| **Code-agnostic core, code-specific rules** | Design codes (ASME B31.4/B31.8, OISD, PNGRB T4S, CSA Z662, ISO 13623, EN 1594) plug in as rule modules. |
| **User-calibratable costs** | Cost data is never hard-coded. The seed values are parametric placeholders. Users load their own unit rates per region and year. |

---

## 2. Suite architecture

```
                 ┌───────────────────────────────────────────────┐
                 │        Feasibility Specialist Bot (LLM)        │
                 │  intake → plan → call tools → compare → report │
                 └───────────────┬───────────────────────────────┘
                                 │ tool calls (JSON schema)
 ┌──────────────┬────────────────┼───────────────┬───────────────┬──────────────┐
 ▼              ▼                ▼               ▼               ▼              ▼
PipeSpec     Route/Alignment   Hydraulics    Mechanical      Cost Engine    Lifecycle
Library      Engine (GIS)      (liquid/gas)  Design (WT,     (CAPEX/OPEX    Economics
(standards,  least-cost path,  pressure      grade, class    BOQ → cost)    (NPV/LCC,
 grades,     alternatives,     profile,      location,                       concession,
 dims)       crossings)        stations)     hydrotest)                      tariff)
 └──────────────┴────────────────┴───────────────┴───────────────┴──────────────┘
                                 │
                      ┌──────────▼──────────┐
                      │  Optimiser / Study   │  scenario tree, iteration loop,
                      │  Orchestrator        │  sensitivity, ranking
                      └──────────┬──────────┘
                                 ▼
                 Reports (DOCX/PDF/XLSX), maps (GeoJSON/KML), API, UI
```

### Proposed tech stack

| Layer | Choice | Reason |
|---|---|---|
| Language | Python 3.12 | Strong engineering, GIS and optimisation ecosystem |
| Data models | Pydantic v2 | Validated records, JSON schemas reused as bot tool schemas |
| Units | `pint` | Mixed SI/US units (NPS/inch, ksi/MPa, bar/psi) without unit bugs |
| Storage | PostgreSQL + PostGIS (prod), SQLite + SpatiaLite (dev); seed data as version-controlled YAML | Spatial queries, reproducible library |
| GIS | `rasterio`, `geopandas`, `shapely`, `pyproj`, `scikit-image` (`MCP_Geometric` least-cost path), `networkx` | Cost-surface routing |
| Optimisation | `scipy`, `pyomo` / OR-Tools (MILP for discrete diameter, grade and stations) | Mixed discrete/continuous problem |
| API | FastAPI | Serves both the bot and the future UI |
| Bot | Claude API with tool use (Agent SDK), conversation + study state persisted | Tool-calling agent over the engine |
| UI (phase 1) | Streamlit + Folium/MapLibre | Fast to build; later React + MapLibre GL |
| Reports | `python-docx`, `openpyxl`, `matplotlib` | Feasibility report, BOQ, LCC tables |
| Testing | `pytest`, `hypothesis`, worked examples from textbooks/codes as golden tests | Engineering correctness |

### Proposed repository layout

```
pipeline_analytics/
├── docs/                      # this plan, ADRs, methodology notes
├── data/
│   ├── pipespec/              # YAML seed: standards, grades, dimensions, coatings
│   ├── cost/                  # sample unit-rate sets (placeholder, per region/year)
│   └── gis/                   # sample AOI rasters/vectors for tests
├── src/pipeline_analytics/
│   ├── pipespec/              # models, loader, query API, validators
│   ├── codes/                 # B31.4, B31.8, OISD-141/226, T4S, CSA Z662 rule modules
│   ├── mechanical/            # wall thickness, class location, hydrotest, buckling checks
│   ├── hydraulics/            # liquid (Darcy–Weisbach), gas (GFE, Panhandle, Weymouth, AGA)
│   ├── routing/               # cost surface, least-cost path, alternatives, crossings
│   ├── cost/                  # BOQ builder, CAPEX, OPEX
│   ├── economics/             # cash flows, NPV/LCC, concession model, tariff, sensitivity
│   ├── optimiser/             # iteration loop, scenario tree, ranking
│   ├── bot/                   # agent, tools, prompts, study memory
│   ├── reports/               # docx/xlsx/map export
│   └── api/                   # FastAPI app
├── apps/streamlit/            # first UI
└── tests/
```

---

## 3. Module A: PipeSpec Library

### 3.1 Coverage (phased)

**Tier 1: steel line pipe for hydrocarbons (MVP)**

| Standard | Content captured |
|---|---|
| **API 5L / ISO 3183** | PSL1 & PSL2; grades L175/A25 → L830/X120 (A, B, X42, X46, X52, X56, X60, X65, X70, X80, X90, X100, X120); delivery conditions (R, N, Q, M); SMYS/SMTS (min/max for PSL2), max Y/T, chemistry limits, CE (IIW/Pcm), CVN/DWTT requirements, hydrotest, dimensional tolerances; annexes (sour service H, offshore J, strain-based) as flags |
| **ASME B36.10M** | NPS ⅛ to 80: OD, WT by schedule (STD, XS, XXS, Sch 10–160) and plain-end weight |
| **ASME B36.19M** | Stainless pipe dimensions (5S, 10S, 40S, 80S) |
| **CSA Z245.1** | Steel pipe grades/categories (Canada) |
| **EN 10208-2** (legacy) / **ISO 3183** | European line pipe (L245NB … L555MB) |
| **ASTM A106 / A53 / A333 / A312 / A671 / A672** | Station piping, low-temperature and EFW pipe |
| **IS 3589 / IS 1239** | Indian steel pipe (mainly water/low-pressure service) |

**Tier 2: coatings & linings**

ISO 21809-1 (3LPE/3LPP), ISO 21809-2 (FBE), DIN 30670, ISO 21809-5 (concrete weight coating), internal flow coat (API RP 5L2), field-joint coatings (ISO 21809-3).

**Tier 3: non-hydrocarbon cross-country pipe (water, slurry)**

Ductile iron (ISO 2531 / IS 8329), MS/steel water pipe (AWWA C200, IS 3589), PCCP (AWWA C301), GRP (AWWA C950 / IS 12709), HDPE (ISO 4427 / IS 4984).

**Tier 4: fittings & components (later)**

MSS SP-75 / ASME B16.9 fittings, B16.5/B16.47 flanges, induction bends (ISO 15590-1), barred tees, insulating joints.

### 3.2 Data model (core entities)

```
Standard(id, body, number, edition, year, title, scope, superseded_by)
Grade(id, standard_id, designation, alt_designations[], psl, delivery_condition,
      smys_min, smys_max, smts_min, smts_max, yt_max, elongation_formula,
      chemistry{C,Mn,P,S,V,Nb,Ti,...}, ce_iiw_max, ce_pcm_max,
      toughness{cvn_J, test_temp, dwtt_shear_%}, sour_service: bool, clause_ref)
PipeSize(nps, dn, od_mm, od_in)
WallThickness(nps, wt_mm, schedule[], weight_kg_m, standard_id)
ManufacturingProcess(code: SMLS|ERW/HFW|LSAW-UOE|LSAW-JCOE|HSAW, od_range, wt_range,
                     joint_factor_E by code, typical_cost_index)
Coating(id, standard_id, type, thickness_classes, temp_range, cost_index)
Availability(process, grade, od, wt_max, mills/region notes)   # constructability limits
```

### 3.3 Library features

* **Query API**: e.g. "all PSL2 grades ≥ X65 available as HSAW at 36″ with WT ≤ 15.9 mm, sour service."
* **Unit-aware** conversions, weight per metre calculated as `(OD − WT)·WT·0.02466` kg/m for checking.
* **Validation tests**: dimensions are internally consistent, grade strength ordering holds, weights match formula within tolerance.
* **Versioning**: each record carries a standard edition. Studies pin a library version.
* **Bot tools**: `pipespec.search`, `pipespec.get_grade`, `pipespec.standard_wt_series`.

> ⚠️ **Licensing:** Standards (API, ASME, ISO, BIS, CSA) are copyrighted. The library stores **factual values** (strengths, dimensions) with clause references, not reproduced text. Before production use, verify values against **licensed copies** your organisation holds. Each record gets a `verified_by` / `verified_on` field.

---

## 4. Module B: Engineering core used in every iteration

### 4.1 Mechanical design (wall thickness and grade)

* **Gas (ASME B31.8 / OISD-226 / PNGRB T4S):** `t = P·D / (2·S·F·E·T)`, with design factor F by **location class** (Class 1 Div 1 = 0.80, Div 2 = 0.72, Class 2 = 0.60, Class 3 = 0.50, Class 4 = 0.40), joint factor E and temperature derating T.
* **Liquids (ASME B31.4 / OISD-141):** `t = P·D / (2·0.72·E·SMYS)` plus corrosion/mechanical allowances.
* **Class location** is derived automatically from building-density counts along the route (sliding 1.6 km × 400 m window). This is one of the main ways **alignment changes CAPEX**: routes that avoid settlements allow thinner walls.
* **Buried-pipe loads per the American Lifelines Alliance (ALA) *Guidelines for the Design of Buried Steel Pipe*** (see `docs/mechanical-ala.md`, ADR 0005):
  - ALA Appendix B soil springs (axial, lateral, uplift, bearing);
  - earth and surface live load, ring deflection (Iowa) and ring buckling;
  - thermal and combined stress with virtual anchor length;
  - buoyancy and concrete weight coating (water, swamp, liquefied soil);
  - seismic wave propagation, permanent ground deformation (PGD) and strain acceptance.

  Every route segment runs these checks, and the cheapest remedy for a failing check (wall, grade, cover, concrete coating, re-route) flows into CAPEX.
* Other checks: minimum WT for handling (D/t limit), hydrotest pressure and test sections by elevation, external load at road/rail crossings (API RP 1102).
* Output: for each route segment, **(grade, WT, coating)** chosen from the PipeSpec Library and rounded to the next standard WT.

### 4.2 Hydraulics

* **Liquids:** Darcy–Weisbach + Colebrook/Swamee–Jain, viscosity-temperature correction, elevation profile from the DEM, slack-line and MAOP-envelope checks, drag-reducing agent (DRA) option.
* **Gas:** General Flow Equation, Panhandle A/B, Weymouth, AGA fully-turbulent. Compressibility via Papay (screening) or AGA-8/GERG-2008 (detailed). Isothermal assumption first, then a temperature profile.
* **Station siting:** for a given D and MAOP, choose pump/compressor station count and locations that respect min/max pressure along the elevation profile. Hydraulic gradient line plots are produced for the report.
* **Throughput profile:** year-by-year ramp-up over the concession, so stations can be **phased** (added when throughput requires it). This changes the CAPEX/OPEX timing.

### 4.3 Cost engine (BOQ → CAPEX/OPEX)

**CAPEX line items** (each priced from a user-loadable unit-rate set, region + base year + escalation):

| Group | Driver |
|---|---|
| Line pipe | tonnage by grade/WT/process × ₹ or $/t |
| Coating | m² × rate by coating type |
| Laying & construction | km × rate by **terrain class** (flat, rolling, hilly, rock, marsh, urban) × diameter (inch-km basis) |
| Crossings | count × type: HDD / open-cut river, cased/uncased road, rail, canal, foreign pipeline |
| Stations | pump/compressor kW × $/kW, plus terminals, scraper stations, SV stations at code-required spacing |
| ROW & land | ha × land rate by land-use class; compensation for crops and trees |
| CP, SCADA, telecom (OFC), leak detection | km-based and lump sums |
| Engineering, PMC, insurance, contingency, IDC | % of base |

**OPEX line items (annual):** energy (electricity for pumps, fuel gas for compressors, as a function of hydraulics and yearly throughput), station O&M (% of station CAPEX), line O&M (₹ or $/km), ROW patrolling, ILI pigging cycles, CP monitoring, DRA consumption, manpower, insurance, regulatory fees, periodic major overhauls (compressor rebuilds every N hours).

### 4.4 Lifecycle economics over the concession

```
LCC_PV = Σ_t CAPEX_t/(1+r)^t + Σ_t OPEX_t(Q_t, route, D, stations)/(1+r)^t − Salvage/Handback_PV
```

* Inputs: concession period, construction period and capex phasing, discount rate (WACC, real or nominal), escalation per cost group, throughput profile Q_t, energy price path, tax/depreciation (optional), handback conditions.
* Outputs: **PV of LCC**, **levelised cost of transport** (₹/t-km, $/bbl, $/MMBtu), required tariff for a target IRR, payback, plus a sensitivity tornado and Monte Carlo (P10/P50/P90).

---

## 5. Module C: Route / alignment engine

### 5.1 Cost-surface model

Each raster cell (e.g. 30 m) gets a **composite cost per metre**. Wherever possible this is real money (₹ or $/m), not just a weight, so the least-cost path matches the economic objective:

| Layer | Source (open data, global) | Effect |
|---|---|---|
| Terrain/slope | Copernicus GLO-30 / SRTM DEM | Construction cost multiplier; slope > threshold = restricted |
| Land use / land cover | ESA WorldCover, Sentinel-2 derived | ROW cost, construction class |
| Settlements & buildings | OSM, Microsoft/Google Open Buildings | Class location → wall thickness; avoidance buffer |
| Protected areas, forests, wildlife | WDPA, national forest maps | Exclusion or high penalty (clearance time and cost) |
| Water bodies & rivers | OSM, HydroRIVERS | Crossing cost (HDD vs open cut by width) |
| Roads, railways, existing pipelines and power lines | OSM, user data | Crossing costs; **corridor sharing discount** along existing ROW |
| Geohazards | Seismic zones, active faults, landslide susceptibility, liquefaction | Penalty or exclusion; special design cost |
| Soil/rock | SoilGrids, geology maps | Trenching cost (rock blasting) |
| Access | Road distance raster | Logistics cost |
| User constraints | Uploaded KML/shape files | Hard no-go zones, mandatory waypoints |

### 5.2 Path generation

1. **Least-cost path** (Dijkstra/A* on 8- or 16-neighbour graph, `MCP_Geometric`) from origin to destination via mandatory waypoints.
2. **Alternative corridors**: k diverse paths using penalty/iterative-exclusion methods, so the bot can show 3–5 truly different options (for example "north via highway corridor" vs. "direct across hills").
3. **Post-processing**: smooth to constructable bends (min bend radius by D), snap crossings perpendicular, and compute chainage, elevation profile, crossing list and class-location segments.
4. Export to GeoJSON/KML.

### 5.3 Why routing must be iterated with hydraulics

The least-cost *construction* path is not necessarily the least-cost *lifecycle* path. Length costs friction energy and O&M every year of the concession, so the cost surface carries an **OPEX-equivalent penalty per metre of length** (PV of friction energy + line O&M).

*Correction (Phase 4, ADR 0007):* this section originally also proposed a penalty per metre of elevation gain. In a packed line the pressure spent climbing is recovered on descent, so energy depends on net elevation change, which is the same for every route between fixed end points. Elevation therefore affects routing through terrain cost (slope), and through high points that set pump discharge (liquid slack-line). Those are judged by each corridor's full hydraulic and lifecycle evaluation, and the optimiser iterates on that.

---

## 6. Module D: Optimiser and the iteration loop (the heart of the viability study)

### 6.1 Decision variables

* Route alignment (corridor choice + local refinement)
* Nominal diameter D (discrete from the PipeSpec Library)
* Grade and WT per segment (discrete, driven by code and class location)
* MAOP (bounded by code, station equipment and grade)
* Number, location and **phasing** of pump/compressor stations
* Optional: looping/staged capacity, DRA, coating type, crossing method

### 6.2 Objective and constraints

* **Minimise** `LCC_PV` over the concession (from §4.4)
* Subject to: delivery pressure ≥ contract minimum; MAOP ≤ code limit per segment; velocity limits (erosion and noise); min WT; slack-line avoidance; station spacing; constructability (mill availability for D/WT/grade); regulatory exclusions.

### 6.3 Iteration loop

```
 ┌─► 1. Generate/refine corridors (cost surface + OPEX-equivalent penalties)
 │   2. For each corridor: profile, crossings, class locations
 │   3. For each D in candidate set:
 │        a. MAOP & WT/grade per segment (PipeSpec + code module)
 │        b. Hydraulics for each year's throughput → stations & phasing
 │        c. BOQ → CAPEX schedule; energy & O&M → OPEX schedule
 │        d. Economics → LCC_PV, levelised tariff
 │   4. Rank (corridor, D, MAOP, stations) combinations
 │   5. Update cost-surface weights from results (e.g. energy cost per
 │      metre of climb); narrow D range around the economic optimum
 └── 6. Repeat until LCC improvement < tolerance (e.g. 0.5%) or max iterations
     7. Sensitivity & risk on the top 3 → recommendation
```

* Inner loop (D × MAOP × stations for a fixed route): enumeration first (it is small), MILP later.
* Outer loop (route): weight updating plus the k-alternatives method. Each pass is stored as a **scenario node** with its parent, the changed parameters and the result, so the user can see *why* the recommendation moved.

---

## 7. Module E: Feasibility Specialist Bot

### 7.1 Role

A domain expert assistant ("pipeline feasibility engineer") that:

1. **Gathers the brief**: fluid (natural gas, crude, products, LPG, water, slurry, H₂, CO₂), throughput and ramp-up, origin, destination and delivery points, inlet and delivery pressure, concession period, financial parameters, jurisdiction/code, known constraints.
2. **Fills gaps with stated defaults** and flags each one as an assumption.
3. **Runs the study** through tools, explains each iteration in plain language ("Moving to 30″ raised CAPEX by 6% but cut the PV of compressor fuel by 14%, so LCC fell 3.1%").
4. **Answers what-ifs** ("What if the concession is 25 years instead of 30?", "Avoid this forest block", "Use X70 instead of X65") by branching new scenarios.
5. **Produces deliverables**: feasibility report, route maps, BOQ, CAPEX/OPEX/LCC tables, sensitivity, risk register and next-step recommendations (surveys, geotech, environmental studies).

### 7.2 Tools exposed to the LLM (JSON-schema, generated from Pydantic models)

| Tool | Purpose |
|---|---|
| `study.create / study.get / scenario.branch / scenario.compare` | Study state & scenario tree |
| `pipespec.search / pipespec.get_grade / pipespec.wt_series` | Library queries |
| `route.build_cost_surface / route.least_cost_path / route.alternatives / route.profile` | Alignment |
| `mech.wall_thickness / mech.class_location` | Code-based design |
| `hydraulics.liquid / hydraulics.gas / hydraulics.site_stations` | Flow and stations |
| `cost.capex / cost.opex` | BOQ and costing |
| `econ.lcc / econ.tariff / econ.sensitivity / econ.monte_carlo` | Lifecycle economics |
| `optimise.run_iteration / optimise.run_to_convergence` | Iteration loop |
| `report.generate` | DOCX/XLSX/KML outputs |

### 7.3 Guardrails

* The bot **must not invent numbers**. Every figure in an answer comes from a tool result, and the system prompt and output validator enforce citations to tool outputs.
* It clearly marks study level (screening, ±30–50%, AACE Class 5/4) and assumptions.
* It refuses to present results as design approval and recommends a detailed engineering and regulatory review.
* Uses the latest Claude models (Opus-class for planning and reporting, a smaller model for routine sub-tasks), with study state persisted so long studies survive across sessions.

### 7.4 Example session

> **User:** Feasibility for a 6 MMSCMD gas line from Point A to Point B, 25-year concession, ramp up to 10 MMSCMD by year 8.
> **Bot:** Confirms code (PNGRB T4S / ASME B31.8), delivery pressure and discount rate (assumes 10% real if not given). It builds the cost surface, finds 4 corridors (182–214 km) and evaluates 18″/20″/24″/30″ × MAOP 72/92 bar.
> **Iteration 1:** best is 24″ X70, Corridor B, 1 compressor station in year 6, LCC ₹X Cr.
> **Iteration 2:** it re-weights for compression fuel cost. Corridor C (+6 km but 140 m less climb, avoids 2 Class 3 stretches) gives −2.4% LCC.
> **Iteration 3:** converged. Report, map and sensitivity follow.

---

## 8. Delivery roadmap

| Phase | Deliverable | Acceptance criteria |
|---|---|---|
| **0. Foundation** (1–2 wk) | Repo skeleton, packaging, CI (lint, type check, tests), units framework, ADRs | `pytest` green in CI; docs build |
| **1. PipeSpec Library MVP** (3–4 wk) | API 5L/ISO 3183 grades, ASME B36.10M/B36.19M dimensions, process & coating catalogue, query API + CLI | ≥ 95% of B36.10M NPS ½–48 sizes loaded; validation tests pass; query examples documented |
| **2a. Mechanical design (ALA)** ✅ | Pressure design (B31.4/B31.8 + OISD/T4S factors) and ALA buried-pipe checks, `assess_segment()`, Streamlit tab | Every formula matches an independent hand calculation |
| **2b. Class location & hydraulics** ✅ | Location class (B31.8 840.2 / T4S), MAOP along route, liquid & gas hydraulics, station siting, power and fuel | Gas flow equation matches numerical integration within 0.01%; all formulas hand-checked |
| **2c. PGD finite-element model** | Fault crossings, transverse PGD, settlement using the ALA soil springs | Agreement with published benchmark cases |
| **3. Cost & economics** ✅ (placeholder rates) | BOQ builder, CAPEX/OPEX, concession cash-flow model, LCC, tariff, sensitivity, end-to-end option evaluation | BOQ, OPEX, PV, IRR and tariff match hand calculations in tests; economic-diameter curve is U-shaped (interior optimum asserted). An independent spreadsheet reconciliation is still to do once project rates exist |
| **4. Routing engine** ✅ | Cost surface from AOI layers, least-cost path, distinct alternatives, profile/crossing/class extraction, GeoJSON/KML export | On the synthetic sample AOI: ≥ 3 distinct corridors in ≈ 0.3 s; crossings match a manual count; exclusions respected; every corridor evaluated end to end. Real-data loading (OSM/DEM/WDPA) is still to do |
| **5. Optimiser** ✅ | Design search (diameter × grade × MAOP), route iteration loop with convergence, scenario tree, fixed-alignment studies, offtakes, declining supply pressure | Converges on the synthetic AOI; Bhola–Khulna test case run with sensitivities (`studies/bhola_khulna/REPORT.md`) |
| **6. Feasibility Bot** ✅ (live benchmark pending) | Agent, 9 tools over the engine, prompts, study memory, number guardrail, 30-case benchmark, CLI and Streamlit chat | Offline tests pass (scripted client); live benchmark ≥ 90 % to be run with Claude API credentials |
| **7. Reports & UI** ✅ | Word/Excel study reports from one report model, app downloads, bot `generate_report` tool, corridor map view, Bhola documents | End-to-end chat-to-report test passes (scripted client); workbook formulas recalculate to the engine total |
| **8. Hardening** | Monte Carlo, user cost-database management, auth, multi-user projects | — |

---

## 9. Later applications in the suite (same core)

1. **Detailed hydraulic & transient analysis** (surge, packing/line-pack, pigging)
2. **Station design assistant** (pump/compressor selection, driver selection)
3. **Crossing design** (HDD profile and pull-force, API RP 1102 casing/uncased checks)
4. **Cathodic protection design** (ISO 15589-1)
5. **Construction planning** (spreads, schedule, logistics, pipe-yard planning)
6. **Integrity management** (ILI data, corrosion growth, ASME B31G / RSTRENG fitness-for-service, risk-based inspection)
7. **Regulatory & permitting tracker** (ROU Act/PNGRB in India, other jurisdictions)

---

## 10. Key risks & mitigations

| Risk | Mitigation |
|---|---|
| Standards copyright / incorrect values | Store facts + clause refs only; `verified_by` workflow against licensed copies |
| Cost data unreliable or regional | User-loadable unit-rate sets; clearly labelled placeholders; AACE class disclosure |
| GIS data gaps (resolution, outdated land use) | Layer-quality flags; user overrides; recommend field reconnaissance |
| LLM hallucination of numbers | Tool-only numeric policy; output validator; eval suite |
| Combinatorial explosion (routes × D × MAOP × stations) | Coarse-to-fine search; prune dominated options; cache segment results |
| Code/jurisdiction differences | Pluggable code modules; jurisdiction set at study start |

---

## 11. Decisions and open questions

**Decided (2026-11-04):**
* Jurisdiction: **India first** (PNGRB T4S, OISD-STD-141/226, ASME B31.4/B31.8, IS standards). See ADR 0002.
* Fluids: **natural gas and liquid hydrocarbons**. Water, slurry, H₂ and CO₂ are deferred.
* UI: **Streamlit**. See ADR 0003.
* Phase 0 complete: repo skeleton, uv/pyproject packaging, CI (ruff, mypy strict, pytest on Python 3.11–3.13), pint units framework, ADRs 0001–0004.

**Still open:**
1. **Cost data:** in-house unit rates (₹/inch-km, ₹/t pipe, ₹/kW stations) to calibrate the cost engine?
2. **Concession model:** PNGRB-regulated tariff or BOOT/PPP with handback? Tax and depreciation in the MVP?
3. **Deployment:** single-user desktop, internal server or cloud?

* Phase 1 MVP complete: PipeSpec Library with API 5L / ISO 3183 grades, ASME B36.10M NPS ½–48, IS 3589 grades, process and coating catalogue, Python query API, `pipespec` CLI and Streamlit tab. Seed data lives in `src/pipeline_analytics/pipespec/data/` (inside the package so it ships with it). See `docs/pipespec.md` for coverage, verification workflow and the Phase 1b backlog.

* Phase 2a complete: mechanical design per ALA *Guidelines for the Design of Buried Steel Pipe* (ADR 0005, `docs/mechanical-ala.md`).

* Phase 2b complete: class location and hydraulics with station siting (`docs/hydraulics-class-location.md`).

* Phase 3 complete with **placeholder rates** (`docs/cost-economics.md`, ADR 0006): CAPEX/OPEX, concession cash flows, LCC, levelised cost, post-tax tariff, tornado, and the end-to-end economic-diameter curve.

* Phase 4 complete (`docs/routing.md`, ADR 0007). Finding: a gross-climb penalty is physically wrong for packed lines (pressure is recovered on descent), so the router uses CAPEX + friction/O&M per metre, and elevation is judged by each corridor's full hydraulic and lifecycle evaluation.

* Phase 5 complete (`docs/optimiser.md`). Test case: Bhola → Barishal → Khulna gas evacuation (`studies/bhola_khulna/`).

* Phase 6 built (`docs/bot.md`, ADR 0008). The live 30-case benchmark needs Claude API credentials.

* Phase 7 complete (`docs/reports.md`): Word and Excel study reports, chat-to-report, corridor map view.

**Next step:** run the bot benchmark with credentials; then Phase 8 (hardening: Monte Carlo risk, project unit-rate management, multi-user projects). Replace the placeholder rates as soon as project rates are available.
