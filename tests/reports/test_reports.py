from pathlib import Path

import pytest
from docx import Document
from openpyxl import load_workbook
from openpyxl.worksheet.worksheet import Worksheet

from pipeline_analytics.core.jurisdiction import Service
from pipeline_analytics.economics import Concession, OptionInput, ramp_profile
from pipeline_analytics.hydraulics import GasStationInput, Profile
from pipeline_analytics.optimiser import DesignSpace, optimise_design
from pipeline_analytics.reports import Money, SensitivityRow, report_from_search
from pipeline_analytics.reports.charts import lifecycle_cost_png
from pipeline_analytics.reports.docx import write_docx
from pipeline_analytics.reports.xlsx import write_xlsx

BASE = OptionInput(
    service=Service.GAS,
    nps=20,
    design_pressure_pa_g=92e5,
    profile=Profile.flat(120e3, points=25),
    throughput=ramp_profile(6, 9, 5, 15),
    gas_stations=GasStationInput(supply_pressure_pa_g=50e5, delivery_min_pa_g=30e5),
    concession=Concession(operating_years=15),
)


@pytest.fixture(scope="module")
def search():  # type: ignore[no-untyped-def]
    return optimise_design(
        BASE, DesignSpace(sizes=("16", "20", "24", "30"), design_pressures_pa_g=(70e5, 92e5))
    )


@pytest.fixture(scope="module")
def report(search):  # type: ignore[no-untyped-def]
    return report_from_search(
        search,
        title="Test Screening",
        compare_nps="24",
        inputs=[("Length", "120 km")],
        sensitivity=[SensitivityRow(case="Base", best='30" X70', lcc=48.8)],
    )


def test_model_matches_engine(search, report) -> None:  # type: ignore[no-untyped-def]
    money = Money()
    best = search.best
    assert report.best.nps == best.nps
    assert report.best.lcc == pytest.approx(best.lcc_pv / 88e6)
    assert report.capex_total == pytest.approx(best.capex.total / 88e6)
    parts = sum(li.amount for li in report.capex_lines) + sum(v for _, v in report.capex_indirects)
    assert parts == pytest.approx(report.capex_total)
    assert len(report.years) == 3 + 15
    assert sum(y.capex for y in report.years) == pytest.approx(money.m(best.cashflow.total_capex))
    assert [o.nps for o in report.options] == ["16", "20", "24", "30"]
    assert report.compare is not None and report.compare.nps == "24"
    assert any("sensitivity case the optimum" in s for s in report.summary)
    assert "Class 5" in " ".join(report.disclosures)


def test_money_in_crore(search) -> None:  # type: ignore[no-untyped-def]
    r = report_from_search(search, title="x", money=Money.inr_crore())
    assert r.capex_total == pytest.approx(search.best.capex.total / 1e7)
    assert r.tariff_unit == "INR/MMBtu"


def test_chart_png(report) -> None:  # type: ignore[no-untyped-def]
    png = lifecycle_cost_png(report)
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 5000


def test_docx(report, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    path = write_docx(report, tmp_path / "r.docx")
    doc = Document(str(path))
    text = "\n".join(p.text for p in doc.paragraphs)
    headings = (
        "1  Summary",
        "2  Scope and method",
        "4  Results by pipe size",
        "5  Recommended design",
        "6  Sensitivity",
        "Limitations and next steps",
    )
    for heading in headings:  # fmt: skip
        assert heading in text
    assert report.summary[0] in text
    assert len(doc.inline_shapes) == 1  # the chart
    cells = [c.text for t in doc.tables for row in t.rows for c in row.cells]
    assert f"{report.best.lcc:,.1f}" in cells
    assert any(c.startswith("Not feasible") for c in cells)  # 16" fails velocity


def test_xlsx(report, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    path = write_xlsx(report, tmp_path / "r.xlsx")
    wb = load_workbook(path)
    assert wb.sheetnames == [
        "Summary",
        "Options",
        "CAPEX",
        "Years",
        "Sensitivity",
        "Inputs",
        "Method",
    ]
    opts = wb["Options"]
    assert isinstance(opts, Worksheet)
    assert opts.max_row >= 1 + 4
    assert len(opts._charts) == 1  # type: ignore[attr-defined]  # private, no public API
    lcc = {opts.cell(row=r, column=1).value: opts.cell(row=r, column=11).value for r in range(2, 6)}
    assert lcc[report.best.nps] == pytest.approx(report.best.lcc)
    capex = wb["CAPEX"]
    formulas = [c.value for row in capex.iter_rows() for c in row if isinstance(c.value, str)
                and c.value.startswith("=SUM")]  # fmt: skip
    assert len(formulas) == 2  # direct-cost and total sums stay live in Excel
    assert wb["Years"].max_row == 1 + 18


def test_risk_section(search, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    from pipeline_analytics.economics.risk import RiskInputs, monte_carlo
    from pipeline_analytics.reports import risk_note_for, risk_rows

    designs = [i for i, r in search.evaluated if r.feasible][:2]
    mc = monte_carlo(designs, RiskInputs(samples=500))
    rep = report_from_search(search, title="Risk", risk=risk_rows(mc, Money()),
                             risk_note=risk_note_for(mc))  # fmt: skip
    assert any("Monte Carlo samples" in s for s in rep.summary)
    doc = Document(str(write_docx(rep, tmp_path / "r.docx")))
    text = "\n".join(p.text for p in doc.paragraphs)
    # no sensitivity section here, so Risk is 6 and Limitations 7 (numbering is automatic)
    assert "6  Risk (Monte Carlo)" in text and "7  Limitations and next steps" in text
    wb = load_workbook(write_xlsx(rep, tmp_path / "r.xlsx"))
    assert "Risk" in wb.sheetnames
    assert wb["Risk"].cell(row=2, column=6).value == pytest.approx(mc.designs[0].prob_cheapest)
