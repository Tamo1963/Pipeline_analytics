# 0004: One shared pint unit registry; gauge pressure via helpers

**Status:** Accepted (2026-11-04)

## Context
Pipeline data mixes inch/NPS, mm, ksi, MPa, bar, psi, kgf/cm², MMSCMD, MMSCFD, MMTPA and bpd. Unit mistakes are a classic source of design errors.

## Decision
* All quantities crossing module boundaries are `pint` Quantities from the single registry in `core.units` (pint forbids mixing registries).
* Industry units are defined there: `scm`, `MMSCMD`, `MMSCFD`, `MMTPA` (year = 365.25 d), `bpd`, `kgf_cm2`.
* Gauge pressure is **not** a unit. `gauge_to_absolute` / `absolute_to_gauge` make the atmospheric pressure explicit (default 1.01325 bar).
* Mass ↔ volume flow conversions always need an explicit density; no hidden defaults inside the engine.

## Consequences
* Bare numbers are accepted at public APIs via `as_quantity(value, default_unit)` and are dimension-checked.
* MMTPA uses a 365.25-day year. Projects using 365 or 350 operating days must state it as a separate availability factor.
