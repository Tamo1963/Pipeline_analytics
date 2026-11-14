"""Convert a bot :class:`~pipeline_analytics.bot.study.Study` to and from a project payload.

Saved: design basis, route, unit rates, assumptions, the last best result,
sensitivity and risk rows, the scenario tree and (optionally) the chat
transcript. Not saved: the last design search object itself, which is large
and quick to recompute. After loading, run the design search again before
generating a report.
"""

from __future__ import annotations

from typing import Any

from pipeline_analytics.bot.study import DesignBasis, Study
from pipeline_analytics.cost.rates import UnitRates
from pipeline_analytics.optimiser import Alignment
from pipeline_analytics.optimiser.scenarios import ScenarioNode
from pipeline_analytics.reports.model import RiskRow, SensitivityRow

SCHEMA = 1


def study_to_payload(study: Study, chat: list[tuple[str, str]] | None = None) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "basis": study.basis.model_dump(mode="json") if study.basis else None,
        "alignment": study.alignment.model_dump(mode="json") if study.alignment else None,
        "rates": study.rates.model_dump(mode="json"),
        "assumptions": dict(study.assumptions),
        "last_best": study.last_best,
        "sensitivity_rows": [r.model_dump(mode="json") for r in study.sensitivity_rows],
        "risk_rows": [r.model_dump(mode="json") for r in study.risk_rows],
        "risk_note": study.risk_note,
        "scenarios": [n.model_dump(mode="json") for n in study.tree.nodes],
        "chat": [list(m) for m in chat or []],
    }


def payload_to_study(payload: dict[str, Any], study: Study | None = None) -> Study:
    """Fill ``study`` (or a new one) from a payload. Validates every part."""
    if payload.get("schema") != SCHEMA:
        raise ValueError(f"unsupported project payload schema {payload.get('schema')!r}")
    s = study or Study()
    s.basis = DesignBasis.model_validate(payload["basis"]) if payload.get("basis") else None
    s.alignment = (
        Alignment.model_validate(payload["alignment"]) if payload.get("alignment") else None
    )
    s.rates = UnitRates.model_validate(payload["rates"])
    s.assumptions = dict(payload.get("assumptions", {}))
    s.last_best = payload.get("last_best")
    s.sensitivity_rows = [
        SensitivityRow.model_validate(r) for r in payload.get("sensitivity_rows", [])
    ]
    s.risk_rows = [RiskRow.model_validate(r) for r in payload.get("risk_rows", [])]
    s.risk_note = payload.get("risk_note", "")
    s.tree.nodes = [ScenarioNode.model_validate(n) for n in payload.get("scenarios", [])]
    s.last_search = None
    s.last_report_files = {}
    return s


def chat_of(payload: dict[str, Any]) -> list[tuple[str, str]]:
    return [(str(r), str(t)) for r, t in payload.get("chat", [])]


def summary_of(payload: dict[str, Any]) -> dict[str, Any]:
    """Short description for listings."""
    b, a = payload.get("basis") or {}, payload.get("alignment") or {}
    best = payload.get("last_best") or {}
    return {
        "service": b.get("service", ""),
        "route": a.get("name", ""),
        "scenarios": len(payload.get("scenarios", [])),
        "best": f'{best.get("nps")}" {best.get("grade")}' if best.get("nps") else "",
        "placeholder_rates": bool((payload.get("rates") or {}).get("placeholder", True)),
    }
