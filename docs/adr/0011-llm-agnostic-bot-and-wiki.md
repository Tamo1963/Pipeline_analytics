# 0011: LLM-agnostic bot (provider adapters) and a SQLite pipeline wiki

**Status:** Accepted (2026-11-13)

## Context
The feasibility bot was written directly against the Anthropic SDK. The user wants to run it on other LLMs (local or hosted), attach project files (PDF, DOCX, images, KMZ/KML maps) to the conversation, and build a searchable pipeline knowledgebase (wiki).

## Decision
* **Provider adapters, not a framework.** The bot's manual tool loop and number guardrail stay as they are and operate on one internal format: Anthropic-style content blocks. `bot/providers.py` adapts that format per provider:
  * `AnthropicProvider` (default): the official SDK, unchanged request shape (adaptive thinking, `effort`, server-side refusal fallback, cached system prompt).
  * `OpenAIChatProvider`: any OpenAI-compatible `/chat/completions` endpoint over plain `httpx` (OpenAI, Groq, Together, Mistral, vLLM, Ollama). No vendor SDK, so no second dependency tree.
  * Selection by environment (`PIPELINE_BOT_PROVIDER`, `_MODEL`, `_BASE_URL`, `_API_KEY`), so deployments switch without code changes.
* The 30-case benchmark remains the acceptance test for any provider; the engine's numbers do not depend on the model, only the conversation quality does.
* **Uploads are parsed to text client-side** (`bot/uploads.py`): PDF (pypdf), DOCX, text; KMZ/KML placemarks are summarised with great-circle path lengths so drawn routes can seed `define_route`. Images go to the model as image blocks (both providers translate them). Extracted text counts as a guardrail source, like the user's own words. Truncation is explicit, never silent.
* **The wiki is one SQLite file** (`wiki/store.py`): an `articles` table plus an FTS5 index with triggers, `LIKE` fallback where FTS5 is absent. Per-operation connections make it safe across app sessions. A `search_wiki` bot tool and a Wiki tab read and write the same database. SQLite was chosen over the project store's JSON files because full-text search is the point here, and over a vector store because the corpus is small, exact engineering terms matter, and no embedding service dependency is wanted.

## Consequences
* OpenAI-compatible servers vary (token-limit parameter names, reasoning options); `PIPELINE_BOT_TOKENS_PARAM` and `PIPELINE_BOT_REASONING_EFFORT` cover the common differences. Anthropic-specific features (thinking, prompt caching, refusal fallback) do not translate.
* Scanned PDFs yield no text (no OCR dependency); the parser says so.
* Wiki search is keyword-based (bm25), not semantic. If the corpus grows large, an embedding index can be added behind the same store API.
