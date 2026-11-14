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
| `search_wiki` | Full-text search of the pipeline wiki (uploaded documents and articles), or read one article (`docs/bot.md` §Wiki) |
| `lookup_pipe` | Grade strengths (API 5L / ISO 3183), size OD, designated walls, weights, processes |
| `pipe_wall_thickness` | Pressure design (B31.8 / B31.4 with OISD / T4S / Bangladesh factors), orderable wall |
| `reserve_life` | Years a reserve supports a rate, with and without other users |
| `set_design_basis` | Service, flow profile, concession, pressures, fluid, driver, ALA checks; returns the defaults applied |
| `define_route` | Leg-by-leg route (lengths, HDD, crossings, wetland, towns, offtakes) |
| `run_design_search` | Optimiser over size × grade × MAOP; best design and best per size |
| `evaluate_design` | One design in detail: CAPEX breakdown, stations and power by year |
| `run_sensitivity` | Re-optimise under named cases; optionally compare a proposed size |
| `define_route` geohazards | Faults and landslide / lateral-spread / subsidence zones per leg; design search and evaluation return the strain check and mitigation for each |
| `liquefaction_check` | SPT liquefaction triggering, lateral spread at a bank, required HDD exit setback, flotation (`docs/liquefaction.md`) |
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


## Choosing the LLM (provider adapters)

The bot works with any LLM; none is assumed or required. The tool loop and the number guardrail are provider-agnostic (`bot/providers.py`), and the engine's results never depend on the model.

**In the app (left sidebar):**
1. **Provider:** OpenAI, OpenRouter, Groq, Together, Mistral, Ollama Cloud, Ollama (local), vLLM (local), Anthropic (Claude), or Custom (any OpenAI-compatible server).
2. **Endpoint (base URL):** filled in for each provider, editable for proxies, self-hosted servers or regional endpoints.
3. **API key:** the key comes first, because most providers only list their models to a valid key. Leave it empty to use the key from the environment (e.g. `OPENROUTER_API_KEY`); local servers need none. Keys typed here stay in that browser session's memory only.
4. **Model:** filled from the provider's live model list (`GET {base URL}/models`, cached per provider, endpoint and key; **Refresh model list** re-fetches). A rejected key or an unreachable server is reported, and "Custom model name..." always lets you type an id.

Until a provider and model are chosen, the bot tab asks for them. Everything else in the app works without any model. Switching keeps the study (basis, route, results) and restarts the model's conversation context, because message histories do not carry across providers.

**Terminal (`pipeline-bot`):**

```bash
pipeline-bot --provider openrouter --api-key sk-or-... --list-models
pipeline-bot --provider openrouter --api-key sk-or-... --model openai/gpt-5
pipeline-bot --provider ollama --model llama3.1
pipeline-bot --provider custom --base-url https://llm.example.com/v1 --api-key ... --model my-model
```

**Environment** (CLI, benchmark, and the sidebar's starting values):

| Variable | Meaning |
|---|---|
| `PIPELINE_BOT_PROVIDER` | provider name or alias: `openai`, `openrouter`, `groq`, `together`, `mistral`, `ollama`, `ollama-cloud`, `vllm`, `anthropic`, `custom`. If unset, the first provider whose own key variable is set is used; if none is, nothing is selected. |
| `PIPELINE_BOT_MODEL` | model id |
| `PIPELINE_BOT_BASE_URL` | overrides the provider's base URL |
| `PIPELINE_BOT_API_KEY` | the key (else the provider's own variable: `OPENAI_API_KEY`, `OPENROUTER_API_KEY`, `GROQ_API_KEY`, `TOGETHER_API_KEY`, `MISTRAL_API_KEY`, `OLLAMA_API_KEY`, `ANTHROPIC_API_KEY`) |
| `PIPELINE_BOT_TOKENS_PARAM` | `max_tokens` (default) or `max_completion_tokens` (OpenAI reasoning models) |
| `PIPELINE_BOT_REASONING_EFFORT` | sent as `reasoning_effort` when set |

With Anthropic, the bot uses adaptive thinking, `effort=high`, server-side refusal fallback and a cached system prompt. Other providers use plain chat completions with function tools. The 30-case benchmark (`bot/evaluate.py`) was written against Claude; re-run it with your provider before relying on the answers.

## File attachments

The bot tab (and `FeasibilityBot.ask(text, attachments=...)`) takes uploads, parsed by `bot/uploads.py`:

| Type | Handling |
|---|---|
| PDF | text extraction per page (scanned PDFs yield a note, no OCR) |
| DOCX | paragraphs and tables |
| PNG/JPG/GIF/WebP | sent to the model as an image (≤ 4.5 MB) |
| KML / KMZ | placemark summary: paths with great-circle lengths and end coordinates, points, polygons - a drawn route can seed `define_route` |
| TXT / MD / CSV | as text |

Extracted text counts as a guardrail source, so figures quoted from an attached document are accepted. Long files are cut at 60,000 characters with an explicit note.

## Pipeline wiki

A knowledgebase in one SQLite file (`wiki/store.py`; FTS5 full-text index, `$PIPELINE_WIKI_DIR/pipeline_wiki.db`, default `./wiki/`). The app's **Wiki** tab uploads documents (the same parsers as above), writes and edits articles, and searches; the bot reads the same database through `search_wiki`. See ADR 0011.
