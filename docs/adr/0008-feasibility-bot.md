# 0008: Feasibility bot as a tool-calling agent with a number guardrail

**Status:** Accepted (2026-11-05)

## Context
The plan (§7) requires a conversational feasibility engineer that never invents numbers. LLMs can produce plausible but unsupported figures, and feasibility numbers drive investment decisions.

## Decision
* The bot is Claude (Opus 5.5, adaptive thinking, effort high) over the existing engine. It works through nine tools (`bot/tools.py`) that wrap the tested code. Tool schemas come from Pydantic models, and inputs are validated before any engine call.
* A manual tool loop on the Messages API (not the SDK tool runner), so validation, refusal and truncation handling, and the answer check stay in our code.
* **Number guardrail:** every figure in a final answer must appear in a tool result or a user message (within rounding). One automated revision request, then any remaining figure is reported to the user alongside the answer.
* Disclosures (placeholder rates, screening accuracy, defaults applied) are returned by the tools themselves, so the bot always has the text to state.
* Server-side refusal fallback is enabled by default.
* Offline tests use a scripted client. A 30-case live benchmark (`bot/evaluate.py`) measures the plan's ≥ 90 % target.

## Consequences
* Answers can only be as good as the engine's coverage; questions outside it get "no tool gives this".
* Unit conversions (e.g. INR crore) must be done by tools, not by the model; the guardrail flags them otherwise.
* Running the bot needs Claude API credentials and incurs API cost; the engine and Streamlit tabs do not.
