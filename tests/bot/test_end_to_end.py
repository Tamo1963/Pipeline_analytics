"""Phase 7 acceptance: an end-to-end study from chat to report in one session.

A scripted client plays Claude's side; every tool runs the real engine and the
report files are opened and checked.
"""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from docx import Document
from openpyxl import load_workbook

from pipeline_analytics.bot import FeasibilityBot

from .test_agent import FakeClient, resp, text, tool

BASIS = {
    "service": "gas", "jurisdiction": "BD", "throughput_start": 150, "operating_years": 12,
    "supply_pressure_barg": 62, "supply_pressure_end_barg": 40, "delivery_min_barg": 35,
    "design_pressures_barg": [68.95],
}  # fmt: skip
ROUTE = {
    "name": "Demo line",
    "legs": [
        {"name": "A-B", "length_km": 60, "wide_river_hdd_m": 2000, "wetland_fraction": 0.3,
         "end_offtake_share": 0.3, "end_offtake_min_barg": 25, "end_name": "B"},
        {"name": "B-C", "length_km": 80, "minor_roads": 12},
    ],
}  # fmt: skip


class ReadingClient(FakeClient):
    """Writes its final answer from the report tool's actual output."""

    def _create(self, **kw: Any) -> SimpleNamespace:
        if len(self.script) == 1:
            last = kw["messages"][-1]["content"][0]["content"]
            import json

            summary = json.loads(last)["summary"][0]
            self.script = [resp("end_turn", text(f"{summary} The report files are ready."))]
        return super()._create(**kw)


def test_chat_to_report(tmp_path: Path) -> None:
    script = [
        resp("tool_use", tool("t1", "set_design_basis", BASIS), tool("t2", "define_route", ROUTE)),
        resp("tool_use", tool("t3", "run_design_search", {"sizes": ["16", "20", "24"],
                                                           "grades": ["X70"]})),
        resp("tool_use", tool("t4", "run_sensitivity", {
            "cases": [{"name": "Longer concession", "operating_years": 18}],
            "sizes": ["20", "24"], "compare_nps": "24"})),
        resp("tool_use", tool("t4b", "run_risk_analysis", {"samples": 2000})),
        resp("tool_use", tool("t5", "generate_report", {"title": "Demo Line Screening",
                                                         "compare_nps": "24"})),
        resp("end_turn", text("placeholder, replaced from the tool output")),
    ]  # fmt: skip
    bot = FeasibilityBot(ReadingClient(script))
    bot.study.report_dir = tmp_path
    reply = bot.ask("Size a 150 MMSCFD gas line A-B-C and give me the report.")

    names = [n for n, _, _ in reply.tool_calls]
    assert names == ["set_design_basis", "define_route", "run_design_search", "run_sensitivity",
                     "run_risk_analysis", "generate_report"]  # fmt: skip
    assert not any(err for _, _, err in reply.tool_calls)
    assert reply.unsupported == []  # every figure in the answer came from a tool

    docx_path, xlsx_path = (
        tmp_path / "Demo_Line_Screening.docx",
        tmp_path / "Demo_Line_Screening.xlsx",
    )
    assert docx_path.exists() and xlsx_path.exists()
    best = bot.study.last_search.best
    doc_text = "\n".join(p.text for p in Document(str(docx_path)).paragraphs)
    assert f'{best.nps}" {best.grade}' in doc_text
    assert any(p.startswith("7  Risk (Monte Carlo)") for p in doc_text.splitlines())
    assert "Risk" in load_workbook(xlsx_path).sheetnames
    assert "Longer concession" in "\n".join(
        c.text for t in Document(str(docx_path)).tables for r in t.rows for c in r.cells
    )
    wb = load_workbook(xlsx_path)
    inputs = {wb["Inputs"].cell(row=r, column=1).value: wb["Inputs"].cell(row=r, column=2).value
              for r in range(2, 15)}  # fmt: skip
    assert inputs["Route"] == "Demo line: 140 km in 2 leg(s)"
    assert "Offtake B" in inputs
