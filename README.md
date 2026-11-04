# Pipeline Analytics

A suite of applications for **cross-country pipeline** feasibility and design. The first focus is **India** (OISD, PNGRB T4S, ASME B31.4/B31.8) for gas and liquid hydrocarbon service.

* Master plan: [`docs/PLAN.md`](docs/PLAN.md)
* Architecture decisions: [`docs/adr/`](docs/adr/)

## Status

| Phase | Scope | Status |
|---|---|---|
| 0 | Repo skeleton, CI, units framework, ADRs | ✅ done |
| 1 | PipeSpec Library (API 5L / ISO 3183, ASME B36.10M) | next |

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
  pipespec/     pipe specification library          (Phase 1)
  codes/        OISD / T4S / ASME rule modules       (Phase 2)
  mechanical/   wall thickness, class location       (Phase 2)
  hydraulics/   liquid & gas hydraulics, stations    (Phase 2)
  cost/ economics/                                   (Phase 3)
  routing/      GIS least-cost alignment             (Phase 4)
  optimiser/    CAPEX+OPEX iteration loop            (Phase 5)
  bot/          feasibility specialist bot           (Phase 6)
  reports/ api/                                      (Phase 7)
apps/streamlit/ UI
data/           seed data (YAML), sample costs, sample GIS
```
