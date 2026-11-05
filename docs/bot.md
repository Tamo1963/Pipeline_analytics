# Feasibility Bot (Phase 6)

Code: `src/pipeline_analytics/bot/`. Decision record: [ADR 0008](adr/0008-feasibility-bot.md).

A conversational front end for the engine. You describe a project in plain language. The bot stores a design basis and a route, runs the optimiser and sensitivities, and explains the result. **Every number comes from the engine.** The bot does no arithmetic of its own.

## Running it

```bash
uv sync --extra bot
```

```bash
export ANTHROPIC_API_KEY=...   # or: ant auth login
```

```bash
uv run pipeline-bot
```

It also runs in the Streamlit app's **Feasibility bot** tab. Without credentials, the tab explains how to connect.

## Model and request settings

* Claude Opus 5.5 (`claude-opus-5-5`) with adaptive thinking at `effort: "high"`.
* **Server-side refusal fallback** is on (`fallbacks: "default"`, beta `server-side-fallback-2026-07-01`). If a safety classifier declines a turn, the API retries it on a fallback model in the same call. To turn it off, remove `betas`/`fallbacks` in `FeasibilityBot._create`.
* System prompt and tool list are prompt-cached.
* Manual tool loop on the Messages API, not the SDK tool runner, so the bot can:
  * validate every tool input against the engine's own Pydantic models (an invalid input becomes an error result Claude can correct, never an exception);
  * stop on `refusal` and `max_tokens`, never executing tools from a truncated turn;
  * check the final answer's numbers.

## Tools

| Tool | What it does |
|---|---|
| `lookup_pipe` | Grade strengths (API 5L / ISO 3183), size OD, designated walls, weights, processes |
| `pipe_wall_thickness` | Pressure design (B31.8 / B31.4 with OISD / T4S / Bangladesh factors), orderable wall |
| `reserve_life` | Years a reserve supports a rate, with and without other users |
| `set_design_basis` | Service, flow profile, concession, pressures, fluid, driver, ALA checks; returns the defaults applied |
| `define_route` | Leg-by-leg route (lengths, HDD, crossings, wetland, towns, offtakes) |
| `run_design_search` | Optimiser over size × grade × MAOP; best design and best per size |
| `evaluate_design` | One design in detail: CAPEX breakdown, stations and power by year |
| `run_sensitivity` | Re-optimise under named cases; optionally compare a proposed size |
| `define_route` geohazards | Faults and landslide / lateral-spread / subsidence zones per leg; design search and evaluation return the strain check and mitigation for each |
| `ground_movement_check` | Pipe strain from a fault crossing, transverse PGD, settlement or longitudinal PGD (FE model, `docs/mechanical-fe.md`); optional crossing-angle sweep |
| `run_risk_analysis` | Monte Carlo on the last search: lifecycle-cost P10/P50/P90 per size and the probability each is cheapest (see `docs/hardening.md`) |
| `generate_report` | Word report and Excel workbook from the last design search, sensitivity and risk (see `docs/reports.md`) |
| `get_study` | Study state, scenarios evaluated, disclosures |

## Number guardrail

After each answer, every figure is compared with the numbers in the tool results and the user's own messages, allowing for rounding.

* Ignored: small integers below 10 (counts, list numbers), calendar years, standard and code numbers such as B31.8 or ISO 3183, and grade names such as X70.
* If unsupported figures remain, the bot gets **one** automated request to revise (labelled as an automated check, not the user).
* Anything still unsupported is returned in `Reply.unsupported` and shown under the answer in the UI and CLI.

## Evaluation

`src/pipeline_analytics/bot/evals/benchmark.jsonl` holds 30 cases: lookups, wall thickness, reserves, full studies, sensitivities, and behaviour cases (missing information, budget requests, approval requests, off-topic).

A case passes when its answer is grounded, the expected tools were called (or, for missing-information cases, none of the study tools were), and cost answers state the placeholder / screening disclosure. Plan target: **≥ 90 %**.

```bash
uv run python -m pipeline_analytics.bot.evaluate
```

This calls the Claude API and costs money. The full studies run the optimiser (1–3 minutes each).

**Status:** the loop, tools, guardrail and scoring are tested offline with a scripted client. The live benchmark has **not been run yet**, because this build environment has no Claude API credentials.
