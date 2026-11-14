"""LLM-agnostic provider layer: OpenAI translation, env factory, bot loop end to end."""

import json
from typing import Any, ClassVar

import pytest

from pipeline_analytics.bot.agent import FeasibilityBot
from pipeline_analytics.bot.providers import (
    AnthropicProvider,
    Block,
    OpenAIChatProvider,
    env_settings,
    list_models,
    preset_named,
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


ENV_VARS = (
    "PIPELINE_BOT_PROVIDER", "PIPELINE_BOT_MODEL", "PIPELINE_BOT_BASE_URL",
    "PIPELINE_BOT_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY", "GROQ_API_KEY",
    "TOGETHER_API_KEY", "MISTRAL_API_KEY", "OLLAMA_API_KEY", "ANTHROPIC_API_KEY",
)  # fmt: skip


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for var in ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


def test_no_provider_is_assumed(clean_env: pytest.MonkeyPatch) -> None:
    assert env_settings() is None
    with pytest.raises(ValueError, match="no LLM provider configured"):
        provider_from_env()
    assert provider_label() == "no provider configured"


def test_env_picks_the_provider_whose_key_is_set(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("OPENROUTER_API_KEY", "sk-or-test")
    preset, model, base, key = env_settings()  # type: ignore[misc]
    assert preset.name == "OpenRouter" and base == "https://openrouter.ai/api/v1"
    assert key == "sk-or-test" and model == ""
    with pytest.raises(ValueError, match="choose a model"):
        provider_from_env()
    clean_env.setenv("PIPELINE_BOT_MODEL", "openai/gpt-5")
    p = provider_from_env()
    assert isinstance(p, OpenAIChatProvider) and p.api_key == "sk-or-test"


def test_env_named_presets_and_aliases(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("PIPELINE_BOT_PROVIDER", "groq")
    clean_env.setenv("PIPELINE_BOT_MODEL", "llama-3.3-70b-versatile")
    with pytest.raises(ValueError, match="needs an API key"):
        provider_from_env()
    clean_env.setenv("PIPELINE_BOT_API_KEY", "gsk")
    assert provider_from_env().base_url == "https://api.groq.com/openai/v1"  # type: ignore[union-attr]
    clean_env.setenv("PIPELINE_BOT_PROVIDER", "ollama")  # local: no key needed
    clean_env.delenv("PIPELINE_BOT_API_KEY")
    clean_env.setenv("PIPELINE_BOT_MODEL", "llama3.1")
    p = provider_from_env()
    assert p.base_url == "http://localhost:11434/v1" and p.api_key == ""  # type: ignore[union-attr]
    assert preset_named("Ollama Cloud").needs_key  # type: ignore[union-attr]
    clean_env.setenv("PIPELINE_BOT_PROVIDER", "weird")
    with pytest.raises(ValueError, match="unknown"):
        provider_from_env()


def test_list_models_openai_compatible_shapes() -> None:
    seen: list[tuple[str, str]] = []

    def fetch(url: str, key: str, timeout: float) -> Any:
        seen.append((url, key))
        return {"data": [{"id": "z-model"}, {"id": "a-model"}, {"id": "a-model"}]}

    assert list_models("openai", "https://openrouter.ai/api/v1/", "sk", fetch=fetch) == [
        "a-model", "z-model"]  # fmt: skip
    assert seen == [("https://openrouter.ai/api/v1/models", "sk")]
    # Ollama native-style and bare-list payloads
    assert list_models("openai", "http://x/v1", "", fetch=lambda *a: {"models": [
        {"name": "llama3.1"}]}) == ["llama3.1"]  # fmt: skip
    assert list_models("openai", "http://x/v1", "", fetch=lambda *a: ["m1"]) == ["m1"]
    with pytest.raises(ValueError, match="base URL"):
        list_models("openai", "", "k", fetch=fetch)


def test_list_models_rejected_key(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    def fake_get(url: str, headers: dict[str, str], timeout: float) -> httpx.Response:
        assert headers == {"Authorization": "Bearer bad"}
        return httpx.Response(401, text="no", request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", fake_get)
    with pytest.raises(PermissionError, match="rejected"):
        list_models("openai", "https://api.groq.com/openai/v1", "bad")


def test_cli_without_provider_and_list_models(
    clean_env: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from pipeline_analytics.bot import cli, providers

    assert cli.main([]) == 2
    assert "No LLM provider configured" in capsys.readouterr().err
    assert cli.main(["--provider", "openrouter", "--model", "m"]) == 2
    assert "needs an API key" in capsys.readouterr().err
    clean_env.setattr(providers, "list_models", lambda kind, base, key: ["m-1", "m-2"])
    assert cli.main(["--provider", "openrouter", "--api-key", "sk", "--list-models"]) == 0
    assert capsys.readouterr().out.split() == ["m-1", "m-2"]


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
