"""Word report for a study.

Structure: title block, 1 Summary, 2 Scope and method, 3 Inputs and
assumptions, 4 Results by pipe size (with chart), 5 Recommended design
(CAPEX build-up), 6 Sensitivity, 7 Limitations and next steps.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import IO

from docx import Document
from docx.document import Document as DocxDocument
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from pipeline_analytics.reports.charts import lifecycle_cost_png
from pipeline_analytics.reports.model import StudyReport

NAVY = RGBColor(0x12, 0x32, 0x4F)
MUTED = RGBColor(0x4A, 0x5B, 0x70)
FONT = "IBM Plex Sans"
FALLBACK = "Calibri"


def _shade(cell: object, hex_fill: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()  # type: ignore[attr-defined]
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_fill)
    tc_pr.append(shd)


def _style(doc: DocxDocument) -> None:
    normal = doc.styles["Normal"]
    normal.font.name = FONT
    normal.font.size = Pt(10.5)
    rpr = normal.element.get_or_add_rPr()
    fonts = rpr.find(qn("w:rFonts"))
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        rpr.append(fonts)
    for attr in ("w:ascii", "w:hAnsi", "w:cs"):
        fonts.set(qn(attr), FONT)
    for name, size in (("Heading 1", 15), ("Heading 2", 12), ("Title", 24)):
        st = doc.styles[name]
        st.font.name = FONT
        st.font.size = Pt(size)
        st.font.color.rgb = NAVY
        st.font.bold = True


def _numeric(v: str) -> bool:
    try:
        float(v.replace(",", "").replace("%", "").replace("+", "").strip())
    except ValueError:
        return False
    return True


def _table(
    doc: DocxDocument,
    headers: list[str],
    rows: list[list[str]],
    widths_cm: list[float] | None = None,
    highlight: int | None = None,
    span_from: dict[int, int] | None = None,
) -> None:
    """``span_from[row] = col`` merges that row's cells from ``col`` to the end (a note)."""
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = False
    # repeat the header row on each page, and keep rows whole
    tr_pr = t.rows[0]._tr.get_or_add_trPr()
    head = OxmlElement("w:tblHeader")
    head.set(qn("w:val"), "true")
    tr_pr.append(head)
    for c, h in enumerate(headers):
        cell = t.rows[0].cells[c]
        cell.text = ""
        run = cell.paragraphs[0].add_run(h)
        run.bold = True
        run.font.size = Pt(8.5)
        _shade(cell, "EAEEF3")
    for r_i, values in enumerate(rows):
        cells = t.add_row().cells
        for c, v in enumerate(values):
            cells[c].text = ""
            p = cells[c].paragraphs[0]
            run = p.add_run(v)
            run.font.size = Pt(8.5)
            if c > 0 and v and _numeric(v):
                p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            if highlight is not None and r_i == highlight:
                _shade(cells[c], "D8EFEC")
                run.bold = True
    if widths_cm:
        for c, w in enumerate(widths_cm):
            t.columns[c].width = Cm(w)  # grid width (LibreOffice reads this)
        for row in t.rows:
            for c, w in enumerate(widths_cm):
                row.cells[c].width = Cm(w)  # cell width (Word reads this)
    for r_i, col in (span_from or {}).items():
        cells = t.rows[r_i + 1].cells
        text = cells[col].text
        merged = cells[col].merge(cells[-1])
        merged.text = ""
        run = merged.paragraphs[0].add_run(text)
        run.font.size = Pt(8.5)
        run.italic = True


def _note(doc: DocxDocument, text: str) -> None:
    p = doc.add_paragraph()
    run = p.add_run(text)
    run.italic = True
    run.font.size = Pt(9)
    run.font.color.rgb = MUTED


def write_docx(report: StudyReport, path: str | Path | IO[bytes]) -> Path | IO[bytes]:
    """Write the report to a file path or a binary stream (e.g. io.BytesIO)."""
    m = report.money.label
    doc = Document()
    for section in doc.sections:
        section.left_margin = section.right_margin = Cm(2.0)
        section.top_margin = section.bottom_margin = Cm(2.0)
    _style(doc)

    doc.add_paragraph(report.title, style="Title")
    if report.subtitle:
        p = doc.add_paragraph(report.subtitle)
        p.runs[0].font.color.rgb = MUTED
    _note(
        doc,
        f"Screening study prepared {report.prepared_on}. {report.scenarios_evaluated} design "
        "options evaluated. " + " ".join(report.disclosures),
    )

    doc.add_heading("1  Summary", level=1)
    for line in report.summary:
        doc.add_paragraph(line, style="List Bullet")

    doc.add_heading("2  Scope and method", level=1)
    for line in report.method:
        doc.add_paragraph(line, style="List Bullet")

    doc.add_heading("3  Inputs and assumptions", level=1)
    if report.inputs:
        _table(doc, ["Input", "Value"], [[k, v] for k, v in report.inputs], [6.5, 10.5])
    if report.assumptions:
        doc.add_paragraph()
        for a in report.assumptions:
            doc.add_paragraph(a, style="List Bullet")

    doc.add_heading("4  Results by pipe size", level=1)
    doc.add_paragraph(
        f"Best grade and MAOP for each size. Money in {m}; lifecycle cost (LCC) is the present "
        "value of CAPEX plus OPEX over the concession."
    )
    rows, hl, spans = [], None, {}
    for i, r in enumerate(report.options):
        if not r.feasible:
            reason = "gas velocity above the limit" if "velocity" in r.issue else r.issue
            rows.append([f'{r.nps}"', r.grade, f"Not feasible: {reason}", "", "", "", "", ""])
            spans[i] = 2
            continue
        if r.nps == report.best.nps:
            hl = i
        walls = " / ".join(f"{r.walls_mm[k]:g}" for k in ("1-2", "2", "3") if k in r.walls_mm)
        rows.append(
            [
                f'{r.nps}"',
                r.grade,
                f"{r.maop_barg:g}",
                walls,
                f"{r.capex:,.1f}",
                f"{r.pv_opex:,.1f}",
                f"{r.lcc:,.1f}",
                (
                    f"{r.max_stations} stn from yr {r.first_station_year}"
                    if r.first_station_year
                    else "none"
                ),
            ]
        )
    _table(
        doc,
        [
            "NPS",
            "Grade",
            "MAOP barg",
            "Wall mm (C1/C2/C3)",
            f"CAPEX {m}",
            f"PV OPEX {m}",
            f"LCC {m}",
            "Compression",
        ],
        rows,
        [1.3, 1.3, 1.6, 2.8, 1.9, 1.9, 1.9, 4.3],
        hl,
        spans,
    )
    doc.add_paragraph()
    doc.add_picture(io.BytesIO(lifecycle_cost_png(report)), width=Cm(16.5))
    _note(
        doc,
        "Bars: present value of CAPEX (navy) and OPEX (teal); amber marks the lowest "
        "lifecycle cost.",
    )

    b = report.best
    doc.add_heading("5  Recommended design for further study", level=1)
    walls = ", ".join(f"Class {k}: {v:g} mm" for k, v in b.walls_mm.items())
    doc.add_paragraph(
        f'{b.nps}" API 5L {b.grade} PSL2 at {b.maop_barg:g} barg MAOP. Walls {walls} '
        f"(governed by {', '.join(b.wall_governed_by)}). Pipe {b.pipe_tonnes:,.0f} t. "
        f"CAPEX {b.capex:,.1f} {m} ({report.capex_accuracy}). Tariff for the target return "
        f"{b.tariff:,.3f} {report.tariff_unit}; levelised cost "
        f"{b.levelised:,.3f} {report.tariff_unit}."
    )
    groups: dict[str, float] = {}
    for li in report.capex_lines:
        groups[li.group] = groups.get(li.group, 0.0) + li.amount
    rows = [[g, f"{v:,.2f}"] for g, v in groups.items()]
    rows += [[n, f"{v:,.2f}"] for n, v in report.capex_indirects]
    rows.append(["Total CAPEX", f"{report.capex_total:,.2f}"])
    _table(doc, ["CAPEX group", m], rows, [8.0, 4.0], highlight=len(rows) - 1)
    _note(doc, "Line-item quantities and rates are in the accompanying workbook (CAPEX sheet).")

    if report.sensitivity:
        doc.add_heading("6  Sensitivity", level=1)
        doc.add_paragraph("The design is re-optimised in each case.")
        rows = [
            [
                s.case,
                s.best,
                f"{s.lcc:,.1f}",
                "" if s.compare_lcc is None else f"{s.compare_lcc:,.1f}",
                "" if s.compare_premium_pct is None else f"{s.compare_premium_pct:+.1f} %",
            ]
            for s in report.sensitivity
        ]
        _table(
            doc,
            ["Case", "Best design", f"LCC {m}", f"Compared LCC {m}", "Premium"],
            rows,
            [6.2, 3.6, 2.2, 2.6, 2.0],
        )

    doc.add_heading("7  Limitations and next steps", level=1)
    for line in report.disclosures + ([report.money.note] if report.money.note else []):
        doc.add_paragraph(line, style="List Bullet")
    for line in report.next_steps:
        doc.add_paragraph(line, style="List Number")

    if isinstance(path, str | Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        doc.save(str(path))
    else:
        doc.save(path)
    return path
