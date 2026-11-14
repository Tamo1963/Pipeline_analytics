"""LLM providers for the Feasibility Bot (LLM-agnostic).

The bot's tool loop (``agent.py``) works on Anthropic-style *content blocks*
(``text``, ``tool_use``, ``tool_result``, ``image``); each provider translates
between that internal format and its wire format:

* :class:`OpenAIChatProvider` - any OpenAI-compatible ``/chat/completions``
  endpoint over plain HTTPS: OpenAI, OpenRouter, Groq, Together, Mistral,
  Ollama (cloud or local), vLLM, or a custom server.
* :class:`AnthropicProvider` - Claude through the official ``anthropic`` SDK:
  adaptive thinking, ``effort``, server-side refusal fallback, cached system
  prompt.

No provider is assumed. :data:`PRESETS` names the services the app offers;
:func:`list_models` fetches the models an API key can use; and
:func:`env_settings` reads the environment for the CLI and the app's
starting values:

======================================  =============================================
``PIPELINE_BOT_PROVIDER``               preset name or alias (``openrouter``,
                                        ``groq``, ``ollama``, ``openai``,
                                        ``anthropic``, ``custom``, ...); if unset,
                                        the first preset whose key variable is set
``PIPELINE_BOT_MODEL``                  model id
``PIPELINE_BOT_BASE_URL``               overrides the preset's base URL
``PIPELINE_BOT_API_KEY``                key (else the preset's own variable, e.g.
                                        ``OPENROUTER_API_KEY``, ``GROQ_API_KEY``)
``PIPELINE_BOT_TOKENS_PARAM``           ``max_tokens`` (default) or
                                        ``max_completion_tokens``
``PIPELINE_BOT_REASONING_EFFORT``       if set, sent as ``reasoning_effort``
======================================  =============================================

Quality note: the tool loop and the number guardrail work with any provider,
but the bot's tool descriptions and benchmark were tested against Claude.
Re-run ``pipeline_analytics.bot.evaluate`` after switching.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

DEFAULT_ANTHROPIC_MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_TOKENS = 16000


@dataclass
class Block:
    """One content block in the bot's internal (Anthropic-style) format."""

    type: str
    text: str = ""
    id: str = ""
    name: str = ""
    input: dict[str, Any] = field(default_factory=dict)


@dataclass
class ModelResponse:
    content: list[Any]
    stop_reason: str
    model: str = ""


class Provider(Protocol):  # pragma: no cover - typing only
    name: str
    default_model: str

    def create(
        self, *, model: str, system: str, tools: list[dict[str, Any]],
        messages: list[dict[str, Any]], effort: str,
    ) -> Any: ...  # fmt: skip


class AnthropicProvider:
    """Claude through the official SDK (or a scripted stand-in client in tests)."""

    name = "anthropic"

    def __init__(self, client: Any | None = None, default_model: str = DEFAULT_ANTHROPIC_MODEL):
        if client is None:
            import anthropic  # optional dependency: the 'bot' extra

            client = anthropic.Anthropic()
        self.client: Any = client
        self.default_model = default_model

    def create(
        self, *, model: str, system: str, tools: list[dict[str, Any]],
        messages: list[dict[str, Any]], effort: str,
    ) -> Any:  # fmt: skip
        return self.client.beta.messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            tools=tools,
            messages=messages,
            thinking={"type": "adaptive"},
            output_config={"effort": effort},
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )


# ------------------------------------------------- OpenAI-compatible endpoints

_STOP_OF = {"stop": "end_turn", "length": "max_tokens", "content_filter": "refusal",
            "tool_calls": "tool_use"}  # fmt: skip


def _block_type(b: Any) -> str:
    return b.get("type", "") if isinstance(b, dict) else str(getattr(b, "type", ""))


def _attr(b: Any, name: str, default: Any = "") -> Any:
    return b.get(name, default) if isinstance(b, dict) else getattr(b, name, default)


class OpenAIChatProvider:
    """Any OpenAI-compatible chat-completions endpoint, over plain HTTPS.

    No vendor SDK is needed; requests go through ``httpx``. Tool calls map to
    OpenAI *function* tools; the bot's internal history (Anthropic-style
    blocks) is translated on every request, so switching providers only
    changes new conversations, never the engine or the guardrail.
    """

    name = "openai"

    def __init__(
        self,
        *,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        api_key: str = "",
        tokens_param: str = "max_tokens",
        reasoning_effort: str = "",
        post: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        timeout_s: float = 600.0,
    ) -> None:
        self.default_model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.tokens_param = tokens_param
        self.reasoning_effort = reasoning_effort
        self.timeout_s = timeout_s
        self._post = post or self._http_post

    def _http_post(self, payload: dict[str, Any]) -> dict[str, Any]:
        import httpx

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        r = httpx.post(f"{self.base_url}/chat/completions", json=payload, headers=headers,
                       timeout=self.timeout_s)  # fmt: skip
        if r.status_code >= 400:
            raise RuntimeError(f"{self.base_url} returned {r.status_code}: {r.text[:500]}")
        return dict(r.json())

    # ------------------------------------------------------------ translation
    @staticmethod
    def _tools_out(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["input_schema"],
                },
            }
            for t in tools
        ]

    @staticmethod
    def _content_out(blocks: Any) -> list[dict[str, Any]]:
        """User content blocks (text / image) to OpenAI content parts."""
        if isinstance(blocks, str):
            return [{"type": "text", "text": blocks}]
        parts = []
        for b in blocks:
            t = _block_type(b)
            if t == "text":
                parts.append({"type": "text", "text": _attr(b, "text")})
            elif t == "image":
                src = _attr(b, "source", {})
                url = f"data:{src.get('media_type')};base64,{src.get('data')}"
                parts.append({"type": "image_url", "image_url": {"url": url}})
        return parts

    def _messages_out(self, system: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for m in messages:
            content = m["content"]
            if m["role"] == "assistant":
                text = "\n".join(
                    _attr(b, "text") for b in content if _block_type(b) == "text"
                ).strip()
                calls = [
                    {
                        "id": _attr(b, "id"),
                        "type": "function",
                        "function": {
                            "name": _attr(b, "name"),
                            "arguments": json.dumps(_attr(b, "input", {})),
                        },
                    }
                    for b in content
                    if _block_type(b) == "tool_use"
                ]
                msg: dict[str, Any] = {"role": "assistant", "content": text or None}
                if calls:
                    msg["tool_calls"] = calls
                out.append(msg)
                continue
            if isinstance(content, list) and any(_block_type(b) == "tool_result" for b in content):
                for b in content:
                    if _block_type(b) != "tool_result":
                        continue
                    body = str(_attr(b, "content"))
                    if _attr(b, "is_error", False):
                        body = f"[tool error] {body}"
                    out.append({"role": "tool", "tool_call_id": _attr(b, "tool_use_id"),
                                "content": body})  # fmt: skip
                continue
            out.append({"role": m["role"], "content": self._content_out(content)})
        return out

    @staticmethod
    def _response_in(data: dict[str, Any]) -> ModelResponse:
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        blocks: list[Block] = []
        if msg.get("content"):
            blocks.append(Block(type="text", text=str(msg["content"])))
        for call in msg.get("tool_calls") or []:
            fn = call.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except (TypeError, ValueError):
                args = {"_raw": fn.get("arguments")}
            blocks.append(Block(type="tool_use", id=str(call.get("id", "")),
                                name=str(fn.get("name", "")), input=args))  # fmt: skip
        finish = str(choice.get("finish_reason") or "stop")
        stop = "tool_use" if msg.get("tool_calls") else _STOP_OF.get(finish, "end_turn")
        return ModelResponse(content=blocks, stop_reason=stop, model=str(data.get("model", "")))

    def create(
        self, *, model: str, system: str, tools: list[dict[str, Any]],
        messages: list[dict[str, Any]], effort: str,
    ) -> ModelResponse:  # fmt: skip
        payload: dict[str, Any] = {
            "model": model,
            "messages": self._messages_out(system, messages),
            "tools": self._tools_out(tools),
            self.tokens_param: MAX_TOKENS,
        }
        if self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort
        return self._response_in(self._post(payload))


ANTHROPIC_MODELS = ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5")


@dataclass(frozen=True)
class ProviderPreset:
    """A named LLM service for the UI and the CLI."""

    name: str
    kind: str  # "anthropic" | "openai" (OpenAI-compatible chat completions)
    base_url: str
    needs_key: bool
    key_env: str = ""
    """Environment variable that may hold the key (read when the key box is empty)."""
    suggestions: tuple[str, ...] = ()
    """Shown only until the live model list has been fetched."""


PRESETS: tuple[ProviderPreset, ...] = (
    ProviderPreset("OpenAI", "openai", "https://api.openai.com/v1", True, "OPENAI_API_KEY",
                   ("gpt-5.1", "gpt-5", "gpt-4.1")),
    ProviderPreset("OpenRouter", "openai", "https://openrouter.ai/api/v1", True,
                   "OPENROUTER_API_KEY"),
    ProviderPreset("Groq", "openai", "https://api.groq.com/openai/v1", True, "GROQ_API_KEY",
                   ("llama-3.3-70b-versatile",)),
    ProviderPreset("Together", "openai", "https://api.together.xyz/v1", True,
                   "TOGETHER_API_KEY"),
    ProviderPreset("Mistral", "openai", "https://api.mistral.ai/v1", True, "MISTRAL_API_KEY",
                   ("mistral-large-latest",)),
    ProviderPreset("Ollama Cloud", "openai", "https://ollama.com/v1", True, "OLLAMA_API_KEY"),
    ProviderPreset("Ollama (local)", "openai", "http://localhost:11434/v1", False, "",
                   ("llama3.1", "qwen2.5")),
    ProviderPreset("vLLM (local)", "openai", "http://localhost:8000/v1", False),
    ProviderPreset("Anthropic (Claude)", "anthropic", "https://api.anthropic.com", True,
                   "ANTHROPIC_API_KEY", ANTHROPIC_MODELS),
    ProviderPreset("Custom (OpenAI-compatible)", "openai", "", False, "PIPELINE_BOT_API_KEY"),
)  # fmt: skip
PRESET_BY_NAME = {p.name: p for p in PRESETS}


def preset_named(name: str) -> ProviderPreset | None:
    """Look up a preset by its name or a short alias (``openrouter``, ``groq``, ...).

    Exact names win, then the aliases below, then a unique first word.
    """
    key = name.strip().lower()
    for p in PRESETS:
        if key == p.name.lower():
            return p
    alias = {"claude": "Anthropic (Claude)", "anthropic": "Anthropic (Claude)",
             "custom": "Custom (OpenAI-compatible)",
             "openai-compatible": "Custom (OpenAI-compatible)",
             "ollama": "Ollama (local)", "ollama-cloud": "Ollama Cloud",
             "vllm": "vLLM (local)"}.get(key)  # fmt: skip
    if alias:
        return PRESET_BY_NAME[alias]
    hits = [p for p in PRESETS if p.name.lower().split(" ")[0] == key]
    return hits[0] if len(hits) == 1 else None


def env_key(preset: ProviderPreset) -> str:
    return os.environ.get(preset.key_env, "").strip() if preset.key_env else ""


def _get_json(url: str, api_key: str, timeout_s: float) -> Any:
    import httpx

    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    r = httpx.get(url, headers=headers, timeout=timeout_s)
    if r.status_code in (401, 403):
        raise PermissionError(f"the API key was rejected ({r.status_code})")
    if r.status_code >= 400:
        raise RuntimeError(f"{url} returned {r.status_code}: {r.text[:300]}")
    return r.json()


def list_models(
    kind: str,
    base_url: str = "",
    api_key: str = "",
    *,
    timeout_s: float = 15.0,
    fetch: Callable[[str, str, float], Any] | None = None,
) -> list[str]:
    """The model ids a provider offers to this API key (sorted).

    OpenAI-compatible services (OpenAI, OpenRouter, Groq, Together, Mistral,
    Ollama, vLLM) answer ``GET {base_url}/models`` with ``{"data": [{"id": ...}]}``;
    Anthropic is listed through its SDK. Raises ``PermissionError`` for a
    rejected key and ``RuntimeError`` / ``httpx`` errors for other failures.
    """
    kind = kind.strip().lower()
    if kind == "anthropic" and fetch is None:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key or None)
        try:
            return sorted(m.id for m in client.models.list())
        except anthropic.AuthenticationError as e:
            raise PermissionError("the API key was rejected (401)") from e
    if not base_url:
        raise ValueError("enter the endpoint's base URL first")
    url = base_url.rstrip("/") + "/models"
    data = (fetch or _get_json)(url, api_key, timeout_s)
    items = data.get("data", data.get("models", [])) if isinstance(data, dict) else data
    ids = []
    for it in items or []:
        mid = it.get("id") or it.get("name") if isinstance(it, dict) else it
        if mid:
            ids.append(str(mid))
    return sorted(set(ids))


def make_provider(
    kind: str, model: str = "", base_url: str = "", api_key: str = ""
) -> AnthropicProvider | OpenAIChatProvider:
    """Build a provider from plain settings (what the app's sidebar collects)."""
    kind = kind.strip().lower()
    if kind in ("anthropic", "claude"):
        client = None
        if api_key.strip():
            import anthropic

            client = anthropic.Anthropic(api_key=api_key.strip())
        return AnthropicProvider(client, default_model=model or DEFAULT_ANTHROPIC_MODEL)
    if kind in ("openai", "openai-compatible", "openai_compatible"):
        if not model.strip():
            raise ValueError("choose a model")
        if not base_url.strip():
            raise ValueError("enter the endpoint's base URL")
        return OpenAIChatProvider(
            model=model.strip(),
            base_url=base_url.strip(),
            api_key=api_key.strip(),
            tokens_param=os.environ.get("PIPELINE_BOT_TOKENS_PARAM", "max_tokens"),
            reasoning_effort=os.environ.get("PIPELINE_BOT_REASONING_EFFORT", ""),
        )
    raise ValueError(f"unknown provider kind {kind!r}: use 'anthropic' or 'openai'")


def env_settings() -> tuple[ProviderPreset, str, str, str] | None:
    """(preset, model, base_url, api_key) from the environment, or None if nothing is set.

    ``PIPELINE_BOT_PROVIDER`` names a preset (``openrouter``, ``groq``, ``ollama``,
    ``anthropic``, ``custom``, ...) or ``openai`` for any OpenAI-compatible endpoint;
    if it is unset, the first preset whose key variable is set is used. No
    provider is assumed: with nothing configured this returns None.
    """
    name = os.environ.get("PIPELINE_BOT_PROVIDER", "").strip()
    preset = preset_named(name) if name else None
    if name and preset is None:
        raise ValueError(f"unknown PIPELINE_BOT_PROVIDER {name!r}; use one of: "
                         + ", ".join(p.name for p in PRESETS))  # fmt: skip
    if preset is None:
        preset = next((p for p in PRESETS if p.needs_key and env_key(p)), None)
    if preset is None:
        return None
    base = os.environ.get("PIPELINE_BOT_BASE_URL", "").strip() or preset.base_url
    key = os.environ.get("PIPELINE_BOT_API_KEY", "").strip() or env_key(preset)
    model = os.environ.get("PIPELINE_BOT_MODEL", "").strip()
    if not model and preset.kind == "anthropic":
        model = DEFAULT_ANTHROPIC_MODEL
    return preset, model, base, key


def provider_from_env() -> AnthropicProvider | OpenAIChatProvider:
    """The provider the environment selects (see :func:`env_settings`)."""
    st = env_settings()
    if st is None:
        raise ValueError(
            "no LLM provider configured: set PIPELINE_BOT_PROVIDER (e.g. openrouter, groq, "
            "ollama, openai, anthropic, custom), PIPELINE_BOT_MODEL and the provider's API "
            "key (PIPELINE_BOT_API_KEY or e.g. OPENROUTER_API_KEY)"
        )
    preset, model, base, key = st
    if preset.needs_key and preset.kind == "openai" and not key:
        raise ValueError(f"{preset.name} needs an API key ({preset.key_env} or "
                         "PIPELINE_BOT_API_KEY)")  # fmt: skip
    return make_provider(preset.kind, model=model, base_url=base, api_key=key)


def provider_label() -> str:
    """For UIs: which provider/model the environment selects, without connecting."""
    try:
        st = env_settings()
    except ValueError as e:
        return str(e)
    if st is None:
        return "no provider configured"
    preset, model, _base, _key = st
    return f"{preset.name}: {model or '(choose a model)'}"
