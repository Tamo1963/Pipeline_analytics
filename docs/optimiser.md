# Optimiser (Phase 5)

Code: `src/pipeline_analytics/optimiser/`. Objective: **minimum lifecycle cost**, LCC = PV(CAPEX) + PV(OPEX) − PV(residual), over the concession (ADR 0006).

## 1. Design search (fixed alignment)

`optimise_design(base, DesignSpace(sizes, grades, design_pressures_pa_g), workers=4)` evaluates every combination with `evaluate_option`:

* wall per location class: pressure design, D/t, and optionally the ALA buried-pipe checks;
* cheapest manufacturing process;
* hydraulics for every operating year, with **offtakes** and a **year-by-year supply pressure** (e.g. falling field pressure); stations are phased in when first needed;
* CAPEX, OPEX, cash flow and LCC.

Infeasible designs (e.g. erosional velocity) are kept with their reason. `workers` > 1 evaluates in parallel processes; results are identical to a serial run (tested).

## 2. Route iteration (with an AOI)

`optimise_route(aoi, start, end, base, space)` runs the plan §6.3 loop:

1. Build the cost surface for the current best diameter and MAOP, with that option's OPEX-equivalent length penalty.
2. Generate distinct corridors; run the design search on each.
3. Keep the best (corridor, design). Stop when the LCC improvement is below `tol` (0.5 %) or the design repeats; otherwise re-route for the new design.

The router's surface objective approximates LCC but is not identical to it (ADR 0007), so a re-route can come out marginally worse. The optimiser keeps the best result found so far.

## 3. Fixed alignments without GIS data

`Alignment(legs=[Leg(...), ...])` describes a chosen route leg by leg: length, wide-river HDD length, narrow crossings, highways and railways, terrain shares, towns (Class 3/2 bands), and offtakes at leg ends. `alignment.option_input(base)` produces the same input as a routed corridor. Every value is an assumption and should be recorded with its source.

## 4. Scenario tree

Every evaluation is a `ScenarioNode` (parent, iteration, what changed, feasibility, LCC, stations, first compression year). Groups (iterations, corridors, sensitivity cases) are nodes too, so `tree.lineage(id)` shows why the recommendation moved. `tree.to_json()` saves the whole study.

## 5. Engine additions in this phase

* Hydraulics: `Offtake` (share of inlet flow, minimum pressure) for gas and liquids, with flow per segment; station power uses the local flow.
* Options: `offtakes`, `supply_pressure_by_year`; years with zero throughput use no energy.
* Jurisdiction: Bangladesh (ASME B31.8 basis; regulations to confirm).
* Fix: HDD lengths were also costed as open-cut laying and right of use (double count). They are now HDD only.

## 6. Test case

`studies/bhola_khulna/` covers the Bhola → Barishal → Khulna gas evacuation line: `case.yaml` (inputs with sources), `run_study.py`, and `make_report.py`, which produces `REPORT.md`.
