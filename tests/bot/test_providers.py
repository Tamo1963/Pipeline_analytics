"""LLM-agnostic provider layer: OpenAI translation, env factory, bot loop end to end."""

import json
from typing import Any, ClassVar

import pytest

from pipeline_analytics.bot.agent import FeasibilityBot
from pipeline_analytics.bot.providers import (
    AnthropicProvider,
    Block,
    OpenAIChatProvider,
    provider_from_env,
    provider_label,
)

TOOL = {"name": "lookup", "description": "d", "input_schema": {"type": "object"}}


def oai_response(text: str = "", calls: list[tuple[str, str, dict[str, Any]]] | None = None,
                 finish: str = "stop") -> dict[str, Any]:  # fmt: skip
    msg: dict[str, Any] = {"role": "assistant", "content": text or None}
    if calls:
        msg["tool_calls"] = [
            {
                "id": i,
                "type": "function",
                "function": {"name": n, "arguments": json.dumps(a)},
            }
            for i, n, a in calls
        ]
    return {"model": "test-model", "choices": [{"message": msg, "finish_reason": finish}]}


def capture_provider(
    replies: list[dict[str, Any]],
) -> tuple[OpenAIChatProvider, list[dict[str, Any]]]:
    sent: list[dict[str, Any]] = []

    def post(payload: dict[str, Any]) -> dict[str, Any]:
        sent.append(payload)
        return replies.pop(0)

    return OpenAIChatProvider(model="m", api_key="k", post=post), sent


def test_request_translation_tools_system_and_images() -> None:
    p, sent = capture_provider([oai_response("hi")])
    messages = [{"role": "user", "content": [
        {"type": "text", "text": "look"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "QUJD"}},
    ]}]  # fmt: skip
    r = p.create(model="m", system="SYS", tools=[TOOL], messages=messages, effort="high")
    req = sent[0]
    assert req["messages"][0] == {"role": "system", "content": "SYS"}
    parts = req["messages"][1]["content"]
    assert parts[0] == {"type": "text", "text": "look"}
    assert parts[1]["image_url"]["url"] == "data:image/png;base64,QUJD"
    assert req["tools"][0]["function"]["name"] == "lookup"
    assert req["max_tokens"] == 16000 and "reasoning_effort" not in req
    assert r.stop_reason == "end_turn" and r.content[0].text == "hi"


def test_tool_loop_round_trip() -> None:
    """Assistant tool_use -> tool_result -> role 'tool' message on the next request."""
    p, sent = capture_provider([
        oai_response(calls=[("c1", "lookup", {"nps": "24"})], finish="tool_calls"),
        oai_response("done"),
    ])  # fmt: skip
    messages: list[dict[str, Any]] = [{"role": "user", "content": "go"}]
    r1 = p.create(model="m", system="s", tools=[TOOL], messages=messages, effort="high")
    assert r1.stop_reason == "tool_use"
    (use,) = [b for b in r1.content if b.type == "tool_use"]
    assert use.input == {"nps": "24"}
    messages.append({"role": "assistant", "content": r1.content})
    messages.append({"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": use.id, "content": "{\"od\": 610}",
         "is_error": False},
    ]})  # fmt: skip
    r2 = p.create(model="m", system="s", tools=[TOOL], messages=messages, effort="high")
    assert r2.content[0].text == "done"
    req = sent[1]
    assistant = req["messages"][2]
    assert assistant["tool_calls"][0]["function"]["name"] == "lookup"
    assert json.loads(assistant["tool_calls"][0]["function"]["arguments"]) == {"nps": "24"}
    tool_msg = req["messages"][3]
    assert tool_msg == {"role": "tool", "tool_call_id": "c1", "content": '{"od": 610}'}


def test_finish_reason_mapping_and_bad_arguments() -> None:
    p, _ = capture_provider([oai_response("x", finish="length")])
    assert p.create(model="m", system="s", tools=[], messages=[
        {"role": "user", "content": "q"}], effort="high").stop_reason == "max_tokens"  # fmt: skip
    r = OpenAIChatProvider._response_in(
        {"choices": [{"message": {"tool_calls": [
            {"id": "a", "function": {"name": "t", "arguments": "{broken"}}]},
            "finish_reason": "stop"}]})  # fmt: skip
    assert r.stop_reason == "tool_use" and r.content[0].input == {"_raw": "{broken"}


def test_bot_runs_on_openai_provider() -> None:
    """The real FeasibilityBot loop against a scripted OpenAI-style endpoint."""
    p, sent = capture_provider([
        oai_response(calls=[("c1", "reserve_life",
                             {"remaining_reserves_tcf": 1.256, "offtake_mmscfd": 180})],
                     finish="tool_calls"),
        oai_response("The reserve supports 180 MMSCFD for 19.1 years."),
    ])  # fmt: skip
    bot = FeasibilityBot(provider=p)
    reply = bot.ask("How long do 1.256 Tcf last at 180 MMSCFD?")
    assert reply.tool_calls == [("reserve_life",
                                 {"remaining_reserves_tcf": 1.256, "offtake_mmscfd": 180},
                                 False)]  # fmt: skip
    assert reply.unsupported == []  # 19.1 came from the real tool result
    assert "19.1" in reply.text
    assert sent[1]["messages"][0]["role"] == "system"


def test_provider_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("PIPELINE_BOT_PROVIDER", "PIPELINE_BOT_MODEL", "PIPELINE_BOT_BASE_URL",
                "PIPELINE_BOT_API_KEY", "OPENAI_API_KEY"):  # fmt: skip
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("PIPELINE_BOT_PROVIDER", "openai")
    with pytest.raises(ValueError, match="PIPELINE_BOT_MODEL"):
        provider_from_env()
    monkeypatch.setenv("PIPELINE_BOT_MODEL", "llama3.1:70b")
    monkeypatch.setenv("PIPELINE_BOT_BASE_URL", "http://localhost:11434/v1")
    p = provider_from_env()
    assert isinstance(p, OpenAIChatProvider)
    assert p.default_model == "llama3.1:70b" and p.base_url == "http://localhost:11434/v1"
    assert "llama3.1:70b" in provider_label()
    monkeypatch.setenv("PIPELINE_BOT_PROVIDER", "weird")
    with pytest.raises(ValueError, match="unknown"):
        provider_from_env()


def test_anthropic_provider_request_shape() -> None:
    sent: dict[str, Any] = {}

    class C:
        class beta:  # noqa: N801
            class messages:  # noqa: N801
                @staticmethod
                def create(**kw: object) -> object:
                    sent.update(kw)

                    class R:
                        content: ClassVar = [Block(type="text", text="ok")]
                        stop_reason = "end_turn"

                    return R()

    bot = FeasibilityBot(C())
    assert isinstance(bot.provider, AnthropicProvider)
    assert bot.ask("hello").text == "ok"
    assert sent["thinking"] == {"type": "adaptive"}
    assert sent["output_config"] == {"effort": "high"}
    assert sent["fallbacks"] == "default"
    assert sent["system"][0]["cache_control"] == {"type": "ephemeral"}
