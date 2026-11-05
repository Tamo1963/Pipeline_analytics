"""Excel workbook for a study: one sheet per topic, real numbers with units.

Sheets: Summary, Options, CAPEX, Years, Sensitivity, Inputs, Method. The
Options sheet carries a native Excel chart of lifecycle cost by size.
"""

from __future__ import annotations

from pathlib import Path
from typing import IO, Any

from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from pipeline_analytics.reports.model import StudyReport

NAVY = "12324F"
HEAD_FILL = PatternFill("solid", fgColor="EAEEF3")
BEST_FILL = PatternFill("solid", fgColor="D8EFEC")
BOLD = Font(bold=True, color="0F1F33")
TITLE = Font(bold=True, size=14, color=NAVY)
NOTE = Font(italic=True, color="4A5B70")
MONEY = "#,##0.0"
NUM0 = "#,##0"
NUM2 = "#,##0.00"
NUM3 = "0.000"


def _table(
    ws: Worksheet,
    row: int,
    headers: list[str],
    rows: list[list[Any]],
    formats: dict[int, str] | None = None,
) -> int:
    """Write a header row and data rows from ``row``; returns the next free row."""
    for c, h in enumerate(headers, start=1):
        cell = ws.cell(row=row, column=c, value=h)
        cell.font, cell.fill = BOLD, HEAD_FILL
        cell.alignment = Alignment(wrap_text=True, vertical="bottom")
    for r_i, values in enumerate(rows, start=row + 1):
        for c, v in enumerate(values, start=1):
            cell = ws.cell(row=r_i, column=c, value=v)
            if formats and c in formats and isinstance(v, int | float):
                cell.number_format = formats[c]
    return row + len(rows) + 2


def _widths(ws: Worksheet, widths: list[int]) -> None:
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w


def write_xlsx(report: StudyReport, path: str | Path | IO[bytes]) -> Path | IO[bytes]:
    """Write the workbook to a file path or a binary stream (e.g. io.BytesIO)."""
    m = report.money.label
    wb = Workbook()

    # ---- Summary
    ws = wb.active
    assert ws is not None
    ws.title = "Summary"
    ws["A1"], ws["A1"].font = report.title, TITLE
    ws["A2"] = report.subtitle
    ws["A3"] = (
        f"Prepared {report.prepared_on}. {report.scenarios_evaluated} design options evaluated."
    )
    row = 5
    for line in report.summary:
        ws.cell(row=row, column=1, value=line)
        row += 1
    row += 1
    ws.cell(row=row, column=1, value="Disclosures").font = BOLD
    for line in report.disclosures + ([report.money.note] if report.money.note else []):
        row += 1
        ws.cell(row=row, column=1, value=line).font = NOTE
    _widths(ws, [140])

    # ---- Options
    wo = wb.create_sheet("Options")
    headers = [
        "NPS",
        "Grade",
        "MAOP barg",
        "Wall C1 mm",
        "Wall C2 mm",
        "Wall C3 mm",
        "Pipe t",
        f"CAPEX {m}",
        f"PV CAPEX {m}",
        f"PV OPEX {m}",
        f"LCC {m}",
        "Stations",
        "First station yr",
        "Peak power MW",
        f"Levelised {report.tariff_unit}",
        f"Tariff {report.tariff_unit}",
        "Note",
    ]
    rows = []
    for r in report.options:
        if not r.feasible:
            rows.append([r.nps, r.grade, r.maop_barg] + [None] * 13 + [r.issue])
            continue
        rows.append(
            [
                r.nps,
                r.grade,
                r.maop_barg,
                r.walls_mm.get("1-2"),
                r.walls_mm.get("2"),
                r.walls_mm.get("3"),
                round(r.pipe_tonnes),
                r.capex,
                r.pv_capex,
                r.pv_opex,
                r.lcc,
                r.max_stations,
                r.first_station_year,
                r.peak_power_mw,
                r.levelised,
                r.tariff,
                "lowest lifecycle cost" if r.nps == report.best.nps else "",
            ]
        )
    fmt = {7: NUM0, 8: MONEY, 9: MONEY, 10: MONEY, 11: MONEY, 14: NUM2, 15: NUM3, 16: NUM3}
    end = _table(wo, 1, headers, rows, fmt)
    for i, r in enumerate(report.options, start=2):
        if r.nps == report.best.nps:
            for c in range(1, len(headers) + 1):
                wo.cell(row=i, column=c).fill = BEST_FILL
    wo.freeze_panes = "B2"
    _widths(wo, [8, 8, 10, 10, 10, 10, 10, 12, 12, 12, 12, 9, 10, 10, 14, 14, 30])
    feasible = [i for i, r in enumerate(report.options, start=2) if r.feasible]
    if feasible:
        chart = BarChart()
        chart.type, chart.grouping, chart.overlap = "col", "stacked", 100
        chart.title = f"Lifecycle cost by pipe size ({m}, PV)"
        chart.y_axis.title = m
        lo, hi = min(feasible), max(feasible)
        data = Reference(wo, min_col=9, max_col=10, min_row=1, max_row=hi)
        chart.add_data(data, titles_from_data=True)
        chart.set_categories(Reference(wo, min_col=1, min_row=lo, max_row=hi))
        chart.height, chart.width = 9, 18
        wo.add_chart(chart, f"A{end + 1}")

    # ---- CAPEX (best design)
    wc = wb.create_sheet("CAPEX")
    wc["A1"] = f'CAPEX build-up: {report.best.nps}" {report.best.grade} ({report.capex_accuracy})'
    wc["A1"].font = BOLD
    rows = [
        [li.group, li.description, li.quantity, li.unit, li.rate, li.amount]
        for li in report.capex_lines
    ]
    nxt = _table(
        wc,
        3,
        ["Group", "Item", "Quantity", "Unit", f"Rate ({report.money.unit_label})", f"Amount ({m})"],
        rows,
        {3: NUM2, 5: NUM2, 6: "#,##0.000"},
    )
    first, last = 4, 3 + len(rows)
    wc.cell(row=nxt - 1, column=5, value="Direct cost").font = BOLD
    wc.cell(row=nxt - 1, column=6, value=f"=SUM(F{first}:F{last})").number_format = "#,##0.000"
    r_i = nxt
    for name, amount in report.capex_indirects:
        wc.cell(row=r_i, column=5, value=name)
        wc.cell(row=r_i, column=6, value=amount).number_format = "#,##0.000"
        r_i += 1
    wc.cell(row=r_i, column=5, value="Total CAPEX").font = BOLD
    total = wc.cell(row=r_i, column=6, value=f"=SUM(F{nxt - 1}:F{r_i - 1})")
    total.number_format, total.font = "#,##0.000", BOLD
    wc.cell(row=r_i + 1, column=5, value="Total from engine").font = NOTE
    wc.cell(row=r_i + 1, column=6, value=report.capex_total).number_format = "#,##0.000"
    wc.freeze_panes = "A4"
    _widths(wc, [14, 48, 14, 9, 16, 16])

    # ---- Years
    wy = wb.create_sheet("Years")
    rows = [
        [
            y.year,
            y.phase,
            y.throughput,
            y.stations,
            y.peak_power_mw,
            y.capex,
            y.opex,
            y.energy,
            y.net_cost,
        ]
        for y in report.years
    ]
    _table(
        wy,
        1,
        [
            "Year",
            "Phase",
            f"Throughput ({report.throughput_unit})",
            "Stations",
            "Peak power MW",
            f"CAPEX {m}",
            f"OPEX {m}",
            f"of which energy {m}",
            f"Net cost {m}",
        ],
        rows,
        {3: NUM0, 5: NUM2, 6: "#,##0.000", 7: "#,##0.000", 8: "#,##0.000", 9: "#,##0.000"},
    )
    wy.freeze_panes = "C2"
    _widths(wy, [7, 13, 22, 9, 13, 13, 13, 16, 13])

    # ---- Sensitivity
    wsn = wb.create_sheet("Sensitivity")
    rows = [
        [s.case, s.best, s.lcc, s.compare_lcc, s.compare_premium_pct] for s in report.sensitivity
    ]
    _table(
        wsn,
        1,
        ["Case", "Best design", f"LCC {m}", f"Compared design LCC {m}", "Premium %"],
        rows,
        {3: MONEY, 4: MONEY, 5: "0.0"},
    )
    _widths(wsn, [46, 24, 12, 22, 11])

    # ---- Inputs and Method
    wi = wb.create_sheet("Inputs")
    nxt = _table(wi, 1, ["Input", "Value"], [list(kv) for kv in report.inputs])
    wi.cell(row=nxt, column=1, value="Assumptions").font = BOLD
    for i, a in enumerate(report.assumptions, start=nxt + 1):
        wi.cell(row=i, column=1, value=a)
    _widths(wi, [44, 80])
    wm = wb.create_sheet("Method")
    for i, line in enumerate([*report.method, "", "Next steps", *report.next_steps], start=1):
        cell = wm.cell(row=i, column=1, value=line)
        if line == "Next steps":
            cell.font = BOLD
    _widths(wm, [140])

    if isinstance(path, str | Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path
