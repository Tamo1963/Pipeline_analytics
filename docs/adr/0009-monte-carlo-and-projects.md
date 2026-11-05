# 0009: Monte Carlo by exact linearisation; file-based project store

**Status:** Accepted (2026-11-05)

## Context
Phase 8 asks for decision-grade risk ranges (P10/P50/P90, the probability that each size is the cheapest), managed project unit rates, and projects that several people can share without overwriting each other's work. A full design evaluation takes about 0.1 to 1 s. A naive Monte Carlo (10,000 samples × 5 sizes) would take hours.

## Decision
* **Exact linearisation.** For a fixed design (size, grade, MAOP), walls, station count and power do not depend on unit rates. Lifecycle cost is therefore *exactly* linear in each rate group's factor, provided that the groups exclude the percentage items (indirect %, insurance %, station O&M %), which already scale with CAPEX. Each design is evaluated 1 + G times per throughput scenario (G = 6 groups). Sampling after that costs no further evaluations. `check_linearity` and a test confirm agreement with direct evaluation to 1e-12.
* To make the model exact, sustaining (later-year) station CAPEX now uses the same indirect build-up as initial CAPEX (`cost.boq.with_indirects`). Previously it was scaled by the initial estimate's total/direct ratio.
* **Throughput uncertainty is discrete.** Throughput changes the hydraulics (stations, power), so it is modelled as weighted scenarios, each fully evaluated. A design that fails in a scenario (for example, gas velocity at high flow) has infinite cost in those samples. It is reported with its probability of being infeasible.
* **Common random numbers.** All designs see the same samples, so P(cheapest) is a fair comparison.
* **Rate sets are files.** One YAML file per named set in a folder. Each set has metadata (approval, region, validity). Validation, diff and escalation tools are provided, and saves are atomic.
* **Projects are files**, written append-only:
  * one JSON file per version;
  * optimistic concurrency, where each save states the version it was based on;
  * a per-project lock and atomic writes;
  * owner, editor and viewer roles.

  Identity comes from Streamlit's OIDC sign-in when it is configured. Otherwise the app runs in an explicitly unverified local mode.

## Consequences
* Rate groups are sampled independently, but real markets correlate. The spread is indicative, not a calibrated distribution.
* The Bhola study figures move slightly because of the sustaining-CAPEX correction. The study is re-run and republished.
* The file store suits a team on one server or a shared drive with working file locks. It is not a database: if many concurrent users or audit needs appear, the same API can be moved onto PostgreSQL.
* The store trusts the user name it is given. Authentication belongs to the app (OIDC) or the deployment.
