"""Feasibility Specialist Bot: a Claude agent over the engine's tools.

Manual tool loop on the Messages API (not the beta tool runner) so that every
tool input is validated against the engine's own Pydantic models, and so the
final answer can be checked against the numbers the tools returned.

Model and request defaults: Claude Opus 5.5 with adaptive thinking at
``effort="high"``; server-side refusal fallback (``fallbacks="default"``) is
enabled; the system prompt and tool list are cached.

Guardrail: every figure in a final answer must appear in a tool result or in
the user's own messages (allowing for rounding). If not, the bot is asked once
to revise. Anything still unsupported is returned in ``Reply.unsupported``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pipeline_analytics.bot.study import Study
from pipeline_analytics.bot.tools import TOOLS, run_tool

MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"

SYSTEM_PROMPT = """You are a pipeline feasibility engineer for cross-country gas and liquid \
pipelines. You run screening-level viability studies whose goal is the lowest lifecycle cost: \
the present value of CAPEX plus OPEX over the concession period.

How to work:
- Gather the brief: fluid, throughput and how it changes over time, route (legs with lengths, \
river and road crossings, wetland, towns, offtakes), pressures, concession period and \
discount rate, jurisdiction. Ask only for what is missing and matters; otherwise proceed and \
state the defaults that were applied (set_design_basis returns them).
- Store the basis with set_design_basis and the route with define_route, then use \
run_design_search. Use evaluate_design for detail on one design, run_sensitivity to test \
named cases, run_risk_analysis for P10/P50/P90 lifecycle cost and the probability that each \
size is cheapest under cost and throughput uncertainty, ground_movement_check for fault \
crossings, landslides and settlement, liquefaction_check for earthquake liquefaction and \
lateral spreading at river banks, and the lookup tools for quick engineering questions.
- After each run, explain what changed and why in plain language, for example which costs \
rose and which fell between pipe sizes.

Rules:
- Every number you state must come from a tool result or from the user. Do not do your own \
arithmetic, unit conversions or estimates. If you need a number no tool gives, say so.
- Always state the disclosures a tool returns (placeholder unit rates, screening accuracy, \
assumptions and defaults). Never present results as a budget, a final design or a design \
approval; recommend detailed engineering, survey and regulatory review as the next step.
- Cite codes by name (ASME B31.8, B31.4, OISD-STD-141/226, PNGRB T4S, ALA buried-pipe guidelines).
- Be concise: lead with the answer, then the supporting figures, then assumptions and next steps."""

_NUM = re.compile(r"(?<![A-Za-z0-9.\-/])[-+]?\d[\d,]*(?:\.\d+)?")
_NUM_ANY = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?(?:[eE][-+]?\d+)?")
_IGNORED_EXACT = {3183.0, 13443.0, 21809.0, 1102.0, 36.10, 36.19, 600.0, 900.0}


def _numbers(text: str, pattern: re.Pattern[str]) -> list[tuple[float, int]]:
    """(value, decimals) for each number in ``text``."""
    out = []
    for m in pattern.finditer(text):
        s = m.group(0).replace(",", "")
        if s in {"+", "-"}:
            continue
        try:
            v = float(s)
        except ValueError:
            continue
        dec = len(s.split(".")[1]) if "." in s and "e" not in s.lower() else 0
        out.append((v, dec))
    return out


def unsupported_numbers(answer: str, sources: list[str]) -> list[str]:
    """Figures in ``answer`` that no source supports (allowing for rounding)."""
    known = [v for src in sources for v, _ in _numbers(src, _NUM_ANY)]
    bad = []
    for v, dec in _numbers(answer, _NUM):
        av = abs(v)
        if dec == 0 and av < 10:
            continue  # counts, list numbers, small integers
        if 1900 <= av <= 2100 and dec == 0:
            continue  # calendar years
        if av in _IGNORED_EXACT:
            continue  # standard numbers (ISO 3183, ...) and ASME class ratings
        tol = 0.5 * 10 ** (-dec)
        if any(abs(abs(k) - av) <= tol + 1e-9 or abs(abs(k) - av) <= 0.005 * av for k in known):
            continue
        bad.append(f"{v:g}")
    return sorted(set(bad), key=lambda s: float(s))


@dataclass
class Reply:
    text: str
    tool_calls: list[tuple[str, dict[str, Any], bool]] = field(default_factory=list)
    """(tool name, input, is_error) in call order."""
    unsupported: list[str] = field(default_factory=list)
    stop_reason: str = ""
    revised: bool = False


class FeasibilityBot:
    def __init__(
        self,
        client: Any | None = None,
        *,
        model: str = MODEL,
        effort: str = "high",
        max_tool_rounds: int = 12,
        on_tool: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        if client is None:
            import anthropic  # optional dependency: the 'bot' extra

            client = anthropic.Anthropic()
        self.client: Any = client
        self.model = model
        self.effort = effort
        self.max_tool_rounds = max_tool_rounds
        self.on_tool = on_tool
        self.study = Study()
        self.messages: list[dict[str, Any]] = []
        self.sources: list[str] = []
        self.tools = [t.definition() for t in TOOLS]

    def _create(self) -> Any:
        return self.client.beta.messages.create(
            model=self.model,
            max_tokens=16000,
            system=[
                {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
            ],
            tools=self.tools,
            messages=self.messages,
            thinking={"type": "adaptive"},
            output_config={"effort": self.effort},
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )

    def _turn(self, reply: Reply) -> Any:
        """Run the tool loop until the model ends its turn."""
        response = None
        for _ in range(self.max_tool_rounds):
            response = self._create()
            self.messages.append({"role": "assistant", "content": response.content})
            reply.stop_reason = response.stop_reason
            if response.stop_reason in ("end_turn", "stop_sequence", "refusal"):
                return response
            if response.stop_reason == "pause_turn":
                continue
            if response.stop_reason == "max_tokens":
                return response  # a truncated tool input is never executed
            uses = [b for b in response.content if getattr(b, "type", None) == "tool_use"]
            if not uses:
                return response
            results = []
            for b in uses:
                if self.on_tool:
                    self.on_tool(b.name, b.input)
                text, is_error = run_tool(self.study, b.name, b.input)
                reply.tool_calls.append((b.name, dict(b.input), is_error))
                if not is_error:
                    self.sources.append(text)
                results.append({"type": "tool_result", "tool_use_id": b.id, "content": text,
                                "is_error": is_error})  # fmt: skip
            self.messages.append({"role": "user", "content": results})
        reply.stop_reason = "max_tool_rounds"
        return response

    @staticmethod
    def _text(response: Any) -> str:
        if response is None:
            return ""
        return "\n".join(
            b.text for b in response.content if getattr(b, "type", None) == "text"
        ).strip()

    def ask(self, user_text: str) -> Reply:
        """Send one user message; returns the bot's checked answer."""
        self.messages.append({"role": "user", "content": user_text})
        self.sources.append(user_text)
        reply = Reply(text="")
        response = self._turn(reply)
        if reply.stop_reason == "refusal":
            reply.text = (
                self._text(response)
                or "The request was declined by the model's safety checks. Rephrase it as a "
                "pipeline feasibility question."
            )
            return reply
        if reply.stop_reason == "max_tokens":
            reply.text = (
                self._text(response) + "\n\n[Answer cut off: the response hit its length limit.]"
            )
            return reply
        text = self._text(response)
        bad = unsupported_numbers(text, self.sources)
        if bad:
            self.messages.append({"role": "user", "content": (
                "[Automated check, not the user] These figures in your answer do not appear in "
                f"any tool result or user message: {', '.join(bad)}. Revise the answer so every "
                "figure comes from a tool result (call a tool if needed), or remove it."
            )})  # fmt: skip
            response = self._turn(reply)
            reply.revised = True
            text = self._text(response)
            bad = unsupported_numbers(text, self.sources)
        reply.text = text
        reply.unsupported = bad
        return reply


def tool_definitions_json() -> str:
    """The tool list as sent to the API (for docs and review)."""
    return json.dumps([t.definition() for t in TOOLS], indent=1)
