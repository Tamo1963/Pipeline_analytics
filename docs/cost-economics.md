# Cost and Lifecycle Economics

Phase 3 of the plan. Code: `src/pipeline_analytics/cost/` (rates, BOQ, CAPEX, OPEX) and `src/pipeline_analytics/economics/` (cash flows, option evaluation, sensitivity). Decision record: [ADR 0006](adr/0006-cost-and-economics-conventions.md).

> ⚠️ **The bundled unit rates are placeholders** (`cost/data/placeholder_in_2026.yaml`, `placeholder: true`). They are order-of-magnitude values for building and testing the method, not market data. Every estimate carries `placeholder_rates=True`, and the Streamlit tab shows a warning. Load project rates before any number is shared.

## 1. Using your own rates

Copy `src/pipeline_analytics/cost/data/placeholder_in_2026.yaml`, edit the values, set `placeholder: false`, then:

```python
from pipeline_analytics.cost import load_rates

rates = load_rates("my_project_rates_2026.yaml")  # validated on load
```

Pass `rates=rates` to `OptionInput`. No code changes are needed.

## 2. CAPEX (bill of quantities)

| Group | Quantity | Placeholder rate |
|---|---|---|
| Line pipe | tonnes per grade/process segment | ₹90,000/t × grade factor × process factor |
| Coating | external area π D L (m²); concrete weight coating (m³) | 3LPE ₹1,000/m²; CWC ₹15,000/m³ |
| Construction | inch-km by terrain | ₹1.2 lakh/inch-km × terrain factor (flat 1.0 … rock 2.5) |
| Crossings | HDD / open-cut inch-m; roads, rail, canals, pipelines (nos) | HDD ₹2,000/inch-m; NH ₹40 lakh; rail ₹60 lakh |
| Stations | count + installed kW (required × 1.5 standby factor) | compressor ₹50 cr + ₹1.5 lakh/kW; pump ₹20 cr + ₹60,000/kW |
| Facilities | terminals; SV stations at B31.8 846.1.1 spacing by class; scraper stations | ₹30 cr; ₹1.5 cr; ₹4 cr each |
| Systems | CP; SCADA, telecom (OFC), leak detection | ₹2 lakh/km; ₹8 lakh/km |
| Land | right of use (20 m ROW × length by land use), station land | agri ₹3 lakh/ha … urban ₹50 lakh/ha |

Indirects, with D = direct cost excluding land and L = land:

| Item | Formula | Placeholder |
|---|---|---|
| Taxes (GST) | t × D | 18 % |
| Engineering / PMC | e × D | 7 % |
| Owner's cost | o × (D + L) | 2 % |
| Contingency | c × (all of the above) | 10 % |

## 3. OPEX (per operating year)

| Item | Basis | Placeholder |
|---|---|---|
| Energy | pump / electric-compressor kWh × tariff; or gas-turbine fuel scm × gas price | ₹8/kWh; ₹25/scm |
| Line O&M | per km | ₹1.5 lakh/km/yr |
| Station O&M | % of cumulative station CAPEX | 3 %/yr |
| Manpower | base + per station | ₹10 cr + ₹3 cr/station/yr |
| In-line inspection | per km, every N years | ₹1.2 lakh/km every 5 years |
| Insurance | % of cumulative CAPEX | 0.3 %/yr |

## 4. Concession economics

* Years numbered from 1: construction years (CAPEX phased, default 25/45/30 %), then operating years.
* Real terms (base-year prices), real discount rate (default 10 %), end-of-year discounting.
* **Lifecycle cost (the study objective):** `LCC = PV(CAPEX) + PV(OPEX) − PV(residual)`.
* **Levelised cost of transport:** `LCC / PV(throughput)`, in ₹/scm and ₹/MMBtu (GCV 9,500 kcal/scm by default) or ₹/t. This is the constant pre-tax tariff that recovers all costs.
* **Tariff for a target return:** the constant tariff giving NPV = 0 at the target post-tax return (default 12 %, PNGRB-style). It uses straight-line depreciation over the operating period, a 25.17 % corporate tax rate, and loss carry-forward.
* IRR at any tariff (pre- or post-tax).

## 5. Option evaluation and the economic diameter

`evaluate_option(OptionInput)` runs the whole chain for one diameter and grade:

1. **Wall per location class:** pressure design (B31.4 / B31.8 + OISD / T4S factors) and a D/t ≤ 96 limit, rounded up to 0.1 mm. With a `burial` template, the **ALA buried-pipe checks** run at the option's own design pressure and location class, and the wall is increased until they pass. Buoyancy over `wet_length_m` is handled by concrete weight coating sized with the ALA buoyancy check.
2. **Cheapest manufacturing process** that can make the OD and wall.
3. **Hydraulics every operating year:** peak flow (average / load factor 0.9) sizes the stations, and average flow gives the energy. Stations are **phased**: added (as sustaining CAPEX) in the year before they are first needed. Options whose hydraulics fail (e.g. erosional velocity) are marked infeasible with the reason.
4. CAPEX, OPEX and the concession cash flow, giving LCC, levelised cost and tariff.

`economic_diameter_curve(base, sizes)` repeats this per size; `best_option()` picks the lowest LCC.

**Acceptance check (plan Phase 3):** on the 300 km demo route at 6 → 10 MMSCMD over 25 years, with placeholder rates, the curve is U-shaped:

| NPS | PV CAPEX (₹ cr) | PV OPEX (₹ cr) | LCC (₹ cr) | Stations |
|---|---|---|---|---|
| 16–20 | | | infeasible (erosional velocity) | |
| 24 | 1,209 | 843 | 2,051 | 3 |
| 28 | 1,076 | 428 | 1,504 | 2 |
| **30** | **1,067** | **328** | **1,395** | 1 |
| 36 | 1,238 | 176 | 1,414 | 1 |

`tests/economics/test_option.py` asserts an interior optimum.

## 6. Sensitivity

`option_tornado(inp, swing=0.2)` re-runs the full option evaluation at ±20 % for each of: line-pipe price, construction rate, station cost, energy price, throughput and discount rate. It returns the parameters sorted by swing in levelised cost. Monte Carlo (P10/P50/P90) is planned for Phase 8.

## 7. Bugs the tests caught

* The ALA template's pressure field was overridden under the wrong name. `model_copy(update=…)` does not validate, so the checks would silently have run at the template's pressure. The integrator now rebuilds with `model_validate`, and a test spies on the inputs the ALA checks actually receive.
* `inf` limits (e.g. no discharge cap) serialised to JSON `null` and could not be reloaded. Models now write `Infinity`, and a round-trip test covers it.

## 8. Not yet included

* Nominal model (escalation per cost group, inflation). The parameters are in place; real terms only for now.
* Financing (debt/equity, IDC), working capital, and PNGRB tariff specifics (capital employed, normative volumes, economic life).
* Monte Carlo risk analysis; cost curves by region and year.
