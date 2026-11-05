# 0006: Cost and economics conventions

**Status:** Accepted (2026-11-05)

## Context
The study objective is the minimum present value of CAPEX + OPEX over the concession. Results must be comparable between options and traceable to rates the user controls. No project unit rates are available yet.

## Decision
* **Rates are data.** Unit rates live in a validated YAML rate set (`UnitRates`). The bundled set is explicitly a **placeholder** (`placeholder: true`). This is carried into every `CapexEstimate` and shown as a warning in the UI.
* **Real terms**, real discount rate (default 10 %), end-of-year discounting, years numbered from financial close. Construction CAPEX is phased; later stations are sustaining CAPEX in the year before they are needed.
* **Objective:** `LCC = PV(CAPEX) + PV(OPEX) − PV(residual)`. Also reported: levelised cost of transport (pre-tax) and the post-tax tariff for a target return (default 12 %, straight-line depreciation, loss carry-forward).
* **Indirects** build up in a fixed order: taxes and engineering on direct cost, owner's cost on direct + land, contingency on everything.
* **Validated rebuilds:** derived model variants are built with `model_validate`, not `model_copy(update=…)`, so a misspelt field fails loudly.

## Consequences
* Swapping in project rates needs no code change.
* Nominal models, financing structure and PNGRB tariff specifics can be added without changing the LCC objective.
