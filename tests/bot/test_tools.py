"""The bot's tools run the real engine and validate their inputs."""

import json
from typing import Any

import pytest

from pipeline_analytics.bot import TOOL_BY_NAME, TOOLS, Study, run_tool

ROUTE = {
    "name": "Test route",
    "legs": [
        {"name": "A-B", "length_km": 60, "wide_river_hdd_m": 2000, "narrow_river_crossings": 10,
         "national_highways": 1, "wetland_fraction": 0.3,
         "towns": [{"name": "B", "at_km": 60, "class3_km": 2, "class2_km": 1}],
         "end_offtake_share": 0.3, "end_offtake_min_barg": 25, "end_name": "B"},
        {"name": "B-C", "length_km": 80, "minor_roads": 12, "wetland_fraction": 0.2},
    ],
}  # fmt: skip
BASIS = {
    "service": "gas", "jurisdiction": "BD", "throughput_start": 150, "operating_years": 15,
    "supply_pressure_barg": 62, "supply_pressure_end_barg": 40, "delivery_min_barg": 35,
    "design_pressures_barg": [68.95],
}  # fmt: skip


def call(study: Study, name: str, args: dict[str, Any]) -> dict[str, Any]:
    text, err = run_tool(study, name, args)
    assert not err, text
    out: dict[str, Any] = json.loads(text)
    return out


def test_every_tool_has_a_valid_schema() -> None:
    names = [t.name for t in TOOLS]
    assert len(names) == len(set(names))
    for t in TOOLS:
        d = t.definition()
        assert d["input_schema"]["type"] == "object"
        assert len(d["description"]) > 40


def test_lookup_and_wall_thickness() -> None:
    s = Study()
    out = call(s, "lookup_pipe", {"nps": "24", "grade": "X70"})
    assert out["grade"]["smys_mpa"] == 485
    assert out["size"]["od_mm"] == 610
    w = call(s, "pipe_wall_thickness", {"nps": "24", "grade": "X70", "design_pressure_barg": 92})
    assert w["required_wall_mm"] == pytest.approx(
        92e5 * 0.610 / (2 * 0.72 * 485e6) * 1000, abs=0.01
    )
    assert w["orderable_wall_mm_0p1_step"] == 8.1


def test_reserve_life() -> None:
    out = call(Study(), "reserve_life", {"remaining_reserves_tcf": 1.256, "offtake_mmscfd": 180,
                                         "other_uses_mmscfd": 80})  # fmt: skip
    assert out["years_at_pipeline_rate_only"] == 19.1
    assert out["years_including_other_uses"] == 13.2


def test_invalid_input_is_an_error_result_not_an_exception() -> None:
    text, err = run_tool(Study(), "pipe_wall_thickness", {"nps": "24", "design_pressure_barg": -5})
    assert err and "invalid input" in text
    text, err = run_tool(Study(), "lookup_pipe", {"grade": "X99"})
    assert err and "X99" in text
    text, err = run_tool(Study(), "no_such_tool", {})
    assert err


def test_search_needs_basis_and_route() -> None:
    text, err = run_tool(Study(), "run_design_search", {})
    assert err and "set_design_basis" in text


def test_basis_route_search_evaluate_sensitivity() -> None:
    s = Study()
    b = call(s, "set_design_basis", BASIS)
    assert b["throughput_unit"] == "MMSCMD"
    assert "discount_rate" in b["defaults_applied"]  # stated as an assumption
    r = call(s, "define_route", ROUTE)
    assert r["length_km"] == 140
    assert r["offtakes"][0]["name"] == "B"
    assert r["km_by_location_class"]["3"] > 0
    d = call(s, "run_design_search", {"sizes": ["16", "20", "24"], "grades": ["X70"]})
    assert d["best"]["feasible"] and d["best"]["nps"] in {"16", "20", "24"}
    assert any("PLACEHOLDER" in x for x in d["disclosures"])
    e = call(s, "evaluate_design", {"nps": "24"})
    assert e["capex_accuracy"].startswith("AACE Class 5")
    assert len(e["stations_by_year"]) == 15
    sens = call(s, "run_sensitivity", {
        "cases": [{"name": "longer", "operating_years": 20},
                  {"name": "dearer capital", "capital_rate_factor": 2.0}],
        "sizes": ["20", "24"], "compare_nps": "24",
    })  # fmt: skip
    assert [c["case"] for c in sens["cases"]] == ["longer", "dearer capital"]
    assert "compare" in sens["cases"][0]
    risk = call(s, "run_risk_analysis", {
        "samples": 2000,
        "rate_ranges": {"line_pipe": {"low": 0.9, "mode": 1.0, "high": 1.5}},
    })  # fmt: skip
    probs = [d["probability_cheapest_pct"] for d in risk["designs"]]
    assert abs(sum(probs) - 100) < 0.5  # one design is cheapest in every sample
    assert risk["evaluations"] == len(risk["designs"]) * 3 * 7  # 1 + 6 groups, 3 scenarios
    assert "line_pipe 0.9-1-1.5" in risk["method_note"]
    assert len(s.risk_rows) == len(risk["designs"])
    st = call(s, "get_study", {})
    assert st["scenarios_evaluated"] >= 3 + 1 + 4
    assert TOOL_BY_NAME["get_study"].name == "get_study"


def test_risk_analysis_needs_a_search_and_known_sizes() -> None:
    text, err = run_tool(Study(), "run_risk_analysis", {})
    assert err and "run_design_search" in text
    text, err = run_tool(Study(), "run_risk_analysis", {"rate_ranges": {"steel": {
        "low": 1, "mode": 1, "high": 2}}})  # fmt: skip
    assert err and "invalid input" in text


def test_generate_report_needs_a_search() -> None:
    text, err = run_tool(Study(), "generate_report", {"title": "Test"})
    assert err and "run_design_search" in text
