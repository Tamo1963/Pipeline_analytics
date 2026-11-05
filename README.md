# Pipeline Analytics

A suite of applications for **cross-country pipeline** feasibility and design. The first focus is **India** (OISD, PNGRB T4S, ASME B31.4/B31.8) for gas and liquid hydrocarbon service.

* Master plan: [`docs/PLAN.md`](docs/PLAN.md)
* Architecture decisions: [`docs/adr/`](docs/adr/)

## Status

| Phase | Scope | Status |
|---|---|---|
| 0 | Repo skeleton, CI, units framework, ADRs | ✅ done |
| 1 | PipeSpec Library (API 5L / ISO 3183, ASME B36.10M, IS 3589): see [`docs/pipespec.md`](docs/pipespec.md) | ✅ MVP done |
| 2a | Mechanical design per ALA buried-pipe guidelines: see [`docs/mechanical-ala.md`](docs/mechanical-ala.md) | ✅ core checks done |
| 2b | Class location and liquid/gas hydraulics with station siting: see [`docs/hydraulics-class-location.md`](docs/hydraulics-class-location.md) | ✅ done |
| 2c | Pipe–soil FE model: fault crossings, transverse PGD, settlement, crossing-angle sweep; benchmarked against Karamitros et al. (2007): see [`docs/mechanical-fe.md`](docs/mechanical-fe.md) | ✅ done |
| 3 | Cost and lifecycle economics (placeholder rates): see [`docs/cost-economics.md`](docs/cost-economics.md) | ✅ done |
| 4 | Route / alignment engine, with mapped faults and landslides strain-checked, mitigated or realigned: see [`docs/routing.md`](docs/routing.md) | ✅ done |
| 5 | Optimiser (design search, route iteration, scenario tree): see [`docs/optimiser.md`](docs/optimiser.md); test case [`studies/bhola_khulna/REPORT.md`](studies/bhola_khulna/REPORT.md) | ✅ done |
| 6 | Feasibility bot (Claude agent over the engine tools, number guardrail): see [`docs/bot.md`](docs/bot.md) | ✅ built; live benchmark pending credentials |
| 7 | Reports & UI: Word/Excel study reports, chat-to-report, map view: see [`docs/reports.md`](docs/reports.md) | ✅ done |
| 8 | Hardening: Monte Carlo risk, project unit-rate sets (`pipeline-rates`), multi-user projects with sign-in: see [`docs/hardening.md`](docs/hardening.md) | ✅ done |

## Development

Requires [uv](https://docs.astral.sh/uv/) and Python ≥ 3.11.

```bash
uv sync --all-extras
```

```bash
uv run pytest
```

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

Run the Streamlit shell:

```bash
uv run streamlit run apps/streamlit/app.py
```

## Layout

```
src/pipeline_analytics/
  core/         units (pint registry with MMSCMD, MMTPA, kgf/cm², …), jurisdiction & code basis
  pipespec/     pipe specification library + `pipespec` CLI; seed YAML in pipespec/data/
  codes/        location class (B31.8 840.2 / T4S / OISD-226), MAOP along route
  mechanical/   ALA buried-pipe checks: pressure, soil springs, loads, thermal, buoyancy, seismic; FE model for faults / PGD
  hydraulics/   liquid & gas pressure profiles, pump/compressor station siting, power
  cost/         unit-rate sets (placeholder India 2026), rate library + `pipeline-rates` CLI, BOQ, CAPEX, OPEX
  economics/    concession cash flows, LCC, tariff, IRR, sensitivity, Monte Carlo risk, economic-diameter curve
  routing/      AOI layers, cost surface, least-cost path, alternatives, extraction, GeoJSON/KML
  optimiser/    design search, route iteration, scenario tree, fixed-alignment studies
  bot/          feasibility bot: Claude agent, tools, guardrail, benchmark (`pipeline-bot`)
  reports/      study report model; Word report and Excel workbook writers
  projects/     multi-user project store: owners, sharing, append-only versions
  api/          FastAPI service layer (planned)
apps/streamlit/ UI
studies/       worked studies (Bhola - Khulna test case)
data/           sample costs, sample GIS (later phases)
```
