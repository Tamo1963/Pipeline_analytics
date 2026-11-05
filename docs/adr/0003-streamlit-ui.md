# 0003: Streamlit for the first UI

**Status:** Accepted (2026-11-04)

## Context
Users need a chat, a map and scenario-comparison tables quickly. A full web front end would slow down engine development.

## Decision
Use Streamlit (`apps/streamlit/`), installed as the optional `ui` extra. The UI holds no engineering logic; it calls the engine package. Streamlit's `AppTest` is used for UI smoke tests in CI.

## Consequences
* Fast iteration; single-process deployment.
* If multi-user or heavier map work needs it later, the FastAPI layer (`api/`) plus a React/MapLibre front end can replace it without changing the engine.
