# 0001: Python engine first; the bot only calls tools

**Status:** Accepted (2026-11-04)

## Context
Feasibility results (wall thickness, hydraulics, CAPEX/OPEX, NPV) must be correct and traceable. LLMs can produce plausible but wrong numbers.

## Decision
All engineering and economics live in a deterministic, tested Python package (`src/pipeline_analytics`). The Feasibility Bot (Phase 6) plans, asks questions and calls engine functions as tools. Every number it reports comes from a tool result. Tool schemas are generated from the engine's Pydantic models.

## Consequences
* The engine can be used without the bot (Streamlit, API, notebooks).
* Golden tests against published worked examples are the main quality gate.
* Uses Python ≥ 3.11, uv for environments and lock file, ruff, mypy (strict) and pytest in CI.
