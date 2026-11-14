# 0005: ALA Guidelines for the Design of Buried Steel Pipe as the mechanical methodology

**Status:** Accepted (2026-11-04)

## Context
The pressure-design codes (ASME B31.4/B31.8, OISD-STD-141/226, PNGRB T4S) set hoop and combined-stress limits but leave most buried-pipe loads to the engineer: soil loads, live loads, soil restraint, buoyancy and seismic ground movement. Cross-country routes in India cross seismic zones, rivers, wetlands and liquefiable alluvium, so these loads can govern wall thickness, cover, coating and even the route.

## Decision
* The mechanical design module implements the American Lifelines Alliance *Guidelines for the Design of Buried Steel Pipe* (2001, with 2005 addenda) for all buried-pipe loads. Pressure design stays with the governing code.
* Each calculation is a separate tested function returning `CheckResult`s with references. `assess_segment()` runs them all for a route segment.
* The ALA soil springs (Appendix B) are the single source of soil restraint for thermal, buoyancy, seismic and (later) finite-element PGD and fault-crossing models.
* All coefficients and limits are parameters with documented defaults, listed for verification in `docs/mechanical-ala.md`.

## Consequences
* Geohazards and burial conditions can drive pipe selection and cost in the feasibility loop, not just internal pressure.
* Fault crossings, transverse PGD and settlement need a non-linear pipe-soil FE model (Phase 2b).
* Defaults must be verified against the licensed ALA document before designs are issued.
