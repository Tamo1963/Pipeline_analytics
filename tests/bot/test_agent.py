"""The agent loop, driven by a scripted stand-in for the Claude client (no network)."""

from types import SimpleNamespace
from typing import Any

from pipeline_analytics.bot import FeasibilityBot, unsupported_numbers


def text(t: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=t)


def tool(i: str, name: str, inp: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=i, name=name, input=inp)


def resp(stop: str, *blocks: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(content=list(blocks), stop_reason=stop, stop_details=None)


class FakeClient:
    def __init__(self, script: list[SimpleNamespace]) -> None:
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kw: Any) -> SimpleNamespace:
        # snapshot: the bot appends to the same list after the call
        self.calls.append({**kw, "messages": list(kw["messages"])})
        return self.script.pop(0)


def test_request_shape_defaults() -> None:
    fc = FakeClient([resp("end_turn", text("Hello."))])
    FeasibilityBot(fc).ask("hi")
    kw = fc.calls[0]
    assert kw["model"] == "claude-opus-5-5"
    assert kw["thinking"] == {"type": "adaptive"}
    assert kw["output_config"] == {"effort": "high"}
    assert kw["fallbacks"] == "default" and kw["betas"] == ["server-side-fallback-2026-07-01"]
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert {t["name"] for t in kw["tools"]} >= {"run_design_search", "define_route"}


def test_tool_loop_runs_tools_and_answers_from_results() -> None:
    fc = FakeClient([
        resp("tool_use", text("Checking."), tool("t1", "pipe_wall_thickness",
             {"nps": "24", "grade": "X70", "design_pressure_barg": 92})),
        resp("end_turn", text("Required wall is 8.04 mm; order 8.1 mm.")),
    ])  # fmt: skip
    bot = FeasibilityBot(fc)
    r = bot.ask("What wall does 24 inch X70 need at 92 barg?")
    assert r.tool_calls[0][0] == "pipe_wall_thickness" and not r.tool_calls[0][2]
    assert r.unsupported == [] and not r.revised
    # the tool result went back with the matching id, as a user turn
    second = fc.calls[1]["messages"]
    assert second[-1]["role"] == "user"
    assert second[-1]["content"][0]["tool_use_id"] == "t1"
    assert '"orderable_wall_mm_0p1_step": 8.1' in second[-1]["content"][0]["content"]


def test_invalid_tool_input_returns_error_result_and_continues() -> None:
    fc = FakeClient([
        resp("tool_use", tool("t1", "pipe_wall_thickness", {"nps": "24"})),
        resp("end_turn", text("I need the design pressure.")),
    ])  # fmt: skip
    r = FeasibilityBot(fc).ask("wall for 24 inch?")
    assert r.tool_calls[0][2] is True
    res = fc.calls[1]["messages"][-1]["content"][0]
    assert res["is_error"] is True and "invalid input" in res["content"]


def test_fabricated_figure_triggers_one_revision() -> None:
    fc = FakeClient([
        resp(
            "tool_use",
            tool("t1", "reserve_life", {"remaining_reserves_tcf": 1.256, "offtake_mmscfd": 180}),
        ),
        resp("end_turn", text("Reserves last 19.1 years, about 23.7 billion dollars of gas.")),
        resp("end_turn", text("Reserves last 19.1 years at 180 MMSCFD.")),
    ])  # fmt: skip
    r = FeasibilityBot(fc).ask("How long will 1.256 Tcf last at 180 MMSCFD?")
    assert r.revised
    assert r.unsupported == []
    check = fc.calls[2]["messages"][-1]
    assert "Automated check" in check["content"] and "23.7" in check["content"]


def test_unrevised_fabrication_is_flagged() -> None:
    fc = FakeClient([
        resp("end_turn", text("It will cost 412 crore.")),
        resp("end_turn", text("It will cost 412 crore.")),
    ])  # fmt: skip
    r = FeasibilityBot(fc).ask("rough cost?")
    assert r.revised and r.unsupported == ["412"]


def test_refusal_and_truncation() -> None:
    r = FeasibilityBot(FakeClient([resp("refusal")])).ask("x")
    assert r.stop_reason == "refusal" and "declined" in r.text
    fc = FakeClient([resp("max_tokens", tool("t1", "get_study", {}))])
    r = FeasibilityBot(fc).ask("x")
    assert r.tool_calls == []  # a truncated turn's tools are not executed
    assert "cut off" in r.text


def test_tool_round_limit() -> None:
    loop = [resp("tool_use", tool(f"t{i}", "get_study", {})) for i in range(3)]
    r = FeasibilityBot(FakeClient(loop), max_tool_rounds=3).ask("x")
    assert r.stop_reason == "max_tool_rounds" and len(r.tool_calls) == 3


def test_grounding_rules() -> None:
    src = ['{"lcc": 83.34, "years": 19.1, "premium_pct": 16.7}']
    assert unsupported_numbers("LCC US$ 83.3M, 19 years... 17% more", src) == []
    assert unsupported_numbers("Per ASME B31.8 and ISO 3183, X70 pipe, in 2026", src) == []
    assert unsupported_numbers("about 95.0 million", src) == ["95"]
    assert unsupported_numbers("3 stations, step 1", src) == []


def test_benchmark_cases_and_scoring() -> None:
    from pipeline_analytics.bot.agent import Reply
    from pipeline_analytics.bot.evaluate import load_cases, score

    cases = load_cases()
    assert len(cases) == 30 and len({c["id"] for c in cases}) == 30
    from pipeline_analytics.bot import TOOL_BY_NAME

    for c in cases:
        assert set(c["expect_tools_any"]) <= set(TOOL_BY_NAME)
    case = next(c for c in cases if c["id"] == "study-bhola")
    good = Reply(text="Best is 24 inch (placeholder rates).",
                 tool_calls=[("run_design_search", {}, False)])  # fmt: skip
    assert score(case, good)["pass"]
    no_disclosure = Reply(text="Best is 24 inch.", tool_calls=[("run_design_search", {}, False)])
    assert not score(case, no_disclosure)["pass"]
    ask_back = next(c for c in cases if c["id"] == "missing-route")
    assert score(ask_back, Reply(text="Which route?"))["pass"]
    assert not score(ask_back, Reply(text="x", tool_calls=[("run_design_search", {}, False)]))[
        "pass"
    ]
