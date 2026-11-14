# 0007: Router objective and the treatment of elevation

**Status:** Accepted (2026-11-05)

## Context
The plan (§5.3) proposed an OPEX-equivalent penalty per metre of elevation gain, so the router would avoid hills that cost energy over the concession. When the router was built, the first version charged gross climb (descent free), and it was checked against the physics.

In a packed pipeline, gas or liquid, pressure spent climbing is recovered on the way down. Pumping or compression energy depends on the **net** elevation change between the end points, plus friction. Net change is the same for every route between fixed end points. A gross-climb penalty therefore adds a cost that does not exist and would push routes away from hills wrongly.

## Decision
* The router minimises installed cost (cost surface, crossings) plus a **path-additive** OPEX penalty per metre of length (PV of friction energy and line O&M).
* Elevation affects routing only through terrain (slope → rolling/hilly construction cost, steep-slope exclusion). Gross climb is **not** penalised by default; `climb_per_m` stays available as an explicit heuristic.
* Elevation effects that do exist are judged per corridor by the full hydraulic and lifecycle evaluation: high points that set the pump discharge (liquid slack-line), and station placement. `opex_penalties()` reports the PV of lifting the flow by 1 m net for information.

## Consequences
* Corridor ranking by the router is a CAPEX-plus-friction screen. The final ranking uses the lifecycle cost from `evaluate_option` for each corridor.
* The Phase 5 optimiser iterates route ↔ diameter ↔ hydraulics using that lifecycle cost.
