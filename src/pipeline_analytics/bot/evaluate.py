"""Benchmark the Feasibility Bot (live: calls the Claude API and costs money).

    uv run python -m pipeline_analytics.bot.evaluate            # all 30 cases
    uv run python -m pipeline_analytics.bot.evaluate --ids lookup-x70 reserve-1

A case passes when:
* the answer has no figure missing from the tool results or the prompt;
* at least one expected tool was called (cases with no expected tool must call
  none of the study tools, e.g. it should ask for missing information);
* cost-related answers state the placeholder-rate / screening disclosure.

Plan target (Phase 6): >= 90 % of cases pass.
"""

from __future__ import annotations

import argparse
import json
import time
from importlib import resources
from typing import Any

from pipeline_analytics.bot.agent import FeasibilityBot, Reply

STUDY_TOOLS = {"run_design_search", "evaluate_design", "run_sensitivity", "run_risk_analysis"}
DISCLOSURE_WORDS = ("placeholder", "screening", "class 5", "not a budget", "indicative")


def load_cases() -> list[dict[str, Any]]:
    text = (
        resources.files("pipeline_analytics.bot").joinpath("evals", "benchmark.jsonl").read_text()
    )
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def score(case: dict[str, Any], reply: Reply) -> dict[str, Any]:
    called = {name for name, _, err in reply.tool_calls if not err}
    expect = set(case["expect_tools_any"])
    tools_ok = bool(called & expect) if expect else not (called & STUDY_TOOLS)
    grounded = not reply.unsupported
    disclosed = (not case["cost_related"]) or any(w in reply.text.lower() for w in DISCLOSURE_WORDS)
    return {
        "id": case["id"],
        "pass": tools_ok and grounded and disclosed,
        "tools_ok": tools_ok,
        "grounded": grounded,
        "disclosed": disclosed,
        "called": sorted(called),
        "unsupported": reply.unsupported,
        "revised": reply.revised,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", nargs="*")
    ap.add_argument("--out", default="bot_eval_results.json")
    args = ap.parse_args()
    cases = [c for c in load_cases() if not args.ids or c["id"] in args.ids]
    rows = []
    for c in cases:
        t0 = time.perf_counter()
        bot = FeasibilityBot()
        reply = bot.ask(c["prompt"])
        r = score(c, reply)
        r["seconds"] = round(time.perf_counter() - t0, 1)
        r["answer"] = reply.text
        rows.append(r)
        print(f"{'PASS' if r['pass'] else 'FAIL'} {c['id']} ({r['seconds']} s)", flush=True)
    rate = sum(r["pass"] for r in rows) / len(rows)
    print(f"\n{sum(r['pass'] for r in rows)}/{len(rows)} passed ({rate:.0%}); target >= 90 %")
    with open(args.out, "w") as f:
        json.dump({"pass_rate": rate, "cases": rows}, f, indent=1)


if __name__ == "__main__":
    main()
