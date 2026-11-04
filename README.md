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
| 2c | FE model for fault crossings, transverse PGD, settlement | planned |
| 3 | Cost and lifecycle economics | next |

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
  mechanical/   ALA buried-pipe checks: pressure, soil springs, loads, thermal, buoyancy, seismic
  hydraulics/   liquid & gas pressure profiles, pump/compressor station siting, power
  cost/ economics/                                   (Phase 3)
  routing/      GIS least-cost alignment             (Phase 4)
  optimiser/    CAPEX+OPEX iteration loop            (Phase 5)
  bot/          feasibility specialist bot           (Phase 6)
  reports/ api/                                      (Phase 7)
apps/streamlit/ UI
data/           sample costs, sample GIS (later phases)
```
