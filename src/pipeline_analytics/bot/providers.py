"""LLM providers for the Feasibility Bot.

The bot's tool loop (``agent.py``) is provider-agnostic: it works on
Anthropic-style *content blocks* (``text``, ``tool_use``, ``tool_result``,
``image``), and each provider translates between that internal format and its
wire format. Two providers are built in:

* :class:`AnthropicProvider` - Claude through the official ``anthropic`` SDK
  (the default): adaptive thinking, ``effort``, server-side refusal fallback,
  and a cached system prompt, exactly as before.
* :class:`OpenAIChatProvider` - any OpenAI-compatible ``/chat/completions``
  endpoint over plain HTTPS: OpenAI, Azure OpenAI (with a full base URL),
  Groq, Together, Mistral, vLLM, Ollama (``http://localhost:11434/v1``), ...

Selection is by environment (:func:`provider_from_env`):

======================================  =============================================
``PIPELINE_BOT_PROVIDER``               ``anthropic`` (default) or ``openai``
``PIPELINE_BOT_MODEL``                  model name (default ``claude-opus-5-5``;
                                        required for ``openai``)
``PIPELINE_BOT_BASE_URL``               OpenAI-compatible base URL
                                        (default ``https://api.openai.com/v1``)
``PIPELINE_BOT_API_KEY``                key for that endpoint (falls back to
                                        ``OPENAI_API_KEY``; empty is fine for Ollama)
``PIPELINE_BOT_TOKENS_PARAM``           ``max_tokens`` (default) or
                                        ``max_completion_tokens`` (OpenAI reasoning
                                        models reject ``max_tokens``)
``PIPELINE_BOT_REASONING_EFFORT``       if set, sent as ``reasoning_effort``
======================================  =============================================

Quality note: the bot's number guardrail and tool loop work with any provider,
but the engine's tool descriptions and benchmark were written and tested
against Claude. Re-run ``pipeline_analytics.bot.evaluate`` after switching.
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


KNOWN_ENDPOINTS: dict[str, str] = {
    "OpenAI": "https://api.openai.com/v1",
    "Groq": "https://api.groq.com/openai/v1",
    "Together": "https://api.together.xyz/v1",
    "Mistral": "https://api.mistral.ai/v1",
    "Ollama (local)": "http://localhost:11434/v1",
    "vLLM (local)": "http://localhost:8000/v1",
    "Custom": "",
}
ANTHROPIC_MODELS = ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5")
MODEL_SUGGESTIONS: dict[str, tuple[str, ...]] = {
    "OpenAI": ("gpt-5.1", "gpt-5", "gpt-4.1"),
    "Groq": ("llama-3.3-70b-versatile", "llama-3.1-8b-instant"),
    "Together": ("meta-llama/Llama-3.3-70B-Instruct-Turbo",),
    "Mistral": ("mistral-large-latest", "mistral-small-latest"),
    "Ollama (local)": ("llama3.1", "qwen2.5", "mistral"),
    "vLLM (local)": (),
    "Custom": (),
}
"""Dropdown seeds for UIs; model names drift, so a custom entry is always offered."""


def make_provider(
    kind: str, model: str = "", base_url: str = "", api_key: str = ""
) -> AnthropicProvider | OpenAIChatProvider:
    """Build a provider from plain settings (what the app's sidebar collects)."""
    kind = kind.strip().lower()
    if kind in ("anthropic", "claude", ""):
        client = None
        if api_key.strip():
            import anthropic

            client = anthropic.Anthropic(api_key=api_key.strip())
        return AnthropicProvider(client, default_model=model or DEFAULT_ANTHROPIC_MODEL)
    if kind in ("openai", "openai-compatible", "openai_compatible"):
        if not model.strip():
            raise ValueError("an OpenAI-compatible endpoint needs a model name")
        return OpenAIChatProvider(
            model=model.strip(),
            base_url=(base_url or "https://api.openai.com/v1").strip(),
            api_key=api_key.strip(),
            tokens_param=os.environ.get("PIPELINE_BOT_TOKENS_PARAM", "max_tokens"),
            reasoning_effort=os.environ.get("PIPELINE_BOT_REASONING_EFFORT", ""),
        )
    raise ValueError(f"unknown provider {kind!r}: use 'anthropic' or 'openai'")


def provider_from_env() -> AnthropicProvider | OpenAIChatProvider:
    """The provider the environment selects (see the module docstring)."""
    kind = os.environ.get("PIPELINE_BOT_PROVIDER", "anthropic").strip().lower()
    model = os.environ.get("PIPELINE_BOT_MODEL", "").strip()
    if kind in ("anthropic", "claude", ""):
        return AnthropicProvider(default_model=model or DEFAULT_ANTHROPIC_MODEL)
    if kind in ("openai", "openai-compatible", "openai_compatible"):
        if not model:
            raise ValueError("PIPELINE_BOT_PROVIDER=openai needs PIPELINE_BOT_MODEL")
        return make_provider(
            "openai",
            model=model,
            base_url=os.environ.get("PIPELINE_BOT_BASE_URL", "https://api.openai.com/v1"),
            api_key=os.environ.get("PIPELINE_BOT_API_KEY", "")
            or os.environ.get("OPENAI_API_KEY", ""),
        )
    raise ValueError(f"unknown PIPELINE_BOT_PROVIDER {kind!r}: use 'anthropic' or 'openai'")


def provider_label() -> str:
    """For UIs: which provider/model the environment selects, without connecting."""
    kind = os.environ.get("PIPELINE_BOT_PROVIDER", "anthropic").strip().lower() or "anthropic"
    model = os.environ.get("PIPELINE_BOT_MODEL", "").strip()
    if kind in ("anthropic", "claude"):
        return f"Claude ({model or DEFAULT_ANTHROPIC_MODEL})"
    base = os.environ.get("PIPELINE_BOT_BASE_URL", "https://api.openai.com/v1")
    return f"{model or '?'} via {base}"
