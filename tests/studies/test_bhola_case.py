"""The Bhola case file builds valid inputs and one option evaluates (fast check)."""

import importlib.util
from pathlib import Path

import pytest

from pipeline_analytics.economics import evaluate_option

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "bhola_run_study", ROOT / "studies" / "bhola_khulna" / "run_study.py"
)
assert spec and spec.loader
rs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rs)


def test_alignments_match_sources() -> None:
    case = rs.load_case()
    a = rs.build_alignment(case["alignments"]["A_note"])
    b = rs.build_alignment(case["alignments"]["B_gtcl"])
    assert a.length_m == 173_000  # note: 63 + 110 km
    assert b.length_m == 205_000  # TBS / GTCL: 96 + 109 km
    off = a.offtakes()
    assert len(off) == 1 and off[0].chainage_m == 63_000 and off[0].name == "Barishal"
    assert a.route_quantities().terminals == 2 + 1 + 1  # ends + Barishal + junction unit


def test_reserve_check_reproduces_press_19_years() -> None:
    r = rs.reserve_check(rs.load_case())
    assert r["remaining_recoverable_tcf"] == pytest.approx(1.256)
    assert r["years_at_pipeline_rate_only"] == pytest.approx(19.1, abs=0.1)
    assert r["years_if_local_use_continues"] == pytest.approx(13.2, abs=0.1)


def test_one_option_evaluates() -> None:
    case = rs.load_case()
    al = rs.build_alignment(case["alignments"]["A_note"])
    inp = al.option_input(rs.base_input(case, nps="24"))
    r = evaluate_option(inp)
    assert r.feasible
    s = rs.summarise(r, inp, case)
    assert s["nps"] == "24" and s["lcc_usd_m"] > 0
    assert s["first_compression_year"] is not None  # field decline eventually needs compression


def test_capital_rate_scaling() -> None:
    from pipeline_analytics.cost import placeholder_rates

    base = placeholder_rates()
    hi = rs.scale_capital_rates(base, 2.0, hdd=3.0)
    assert hi.laying_per_inch_km == 2 * base.laying_per_inch_km
    assert hi.crossings.hdd_per_inch_m == 6 * base.crossings.hdd_per_inch_m
    assert hi.opex == base.opex  # OPEX rates untouched
