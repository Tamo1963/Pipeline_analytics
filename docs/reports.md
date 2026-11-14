# Reports and UI (Phase 7)

Code: `src/pipeline_analytics/reports/`. Install with the `reports` extra (python-docx, openpyxl, pillow).

## One report model, three outputs

`report_from_search(search, title=..., ...)` builds a `StudyReport` from an optimiser `DesignSearch`. `report_from_options(evaluated, ...)` does the same from any list of evaluated options. The Word report, the Excel workbook and the app all read this one model, so they cannot disagree. Money is converted once, by `Money`: US$ million at a stated exchange rate (the default) or `Money.inr_crore()`.

| Output | Contents |
|---|---|
| **Word** (`write_docx`) | Title block with disclosures; 1 Summary; 2 Scope and method; 3 Inputs and assumptions; 4 Results by pipe size (table, optimum highlighted, chart); 5 Recommended design with CAPEX build-up; 6 Sensitivity; 7 Limitations and next steps |
| **Excel** (`write_xlsx`) | Sheets: Summary, Options (with a native Excel chart), CAPEX (every BOQ line, live `=SUM` subtotals, plus the engine's total as a check), Years (throughput, stations, power, CAPEX, OPEX, energy, net cost), Sensitivity, Inputs, Method |
| **App** | Economics tab: Word/Excel downloads of the current diameter study. Bot tab: downloads after the bot writes a report |

Both writers accept a file path or a binary stream (`io.BytesIO`), so the app can serve downloads without temporary files. Styling follows the owner's engineering design system: navy and teal, and IBM Plex Sans with Calibri as the fallback.

Checks: the tests compare report totals with the engine. In the Bhola workbook, LibreOffice recalculation of the CAPEX sheet's formulas gives US$ 73.629M, exactly the engine's total.

## Chat to report

The bot's `generate_report` tool writes both files from the study's last design search and sensitivity run. `tests/bot/test_end_to_end.py` is the Phase 7 acceptance test. A scripted conversation does six steps:

1. sets the basis;
2. defines a route;
3. runs the design search;
4. runs a sensitivity;
5. generates the report;
6. answers from the tool's own summary.

The test then opens both files and checks their contents against the engine.

## Map view

The Route tab shows the corridors on an interactive basemap (pydeck), converted from the grid CRS to WGS84 with pyproj. GeoJSON and KML downloads remain.

## Bhola test case documents

`uv run python studies/bhola_khulna/make_docs.py` writes `studies/bhola_khulna/reports/Bhola_Khulna_Screening.docx` and `.xlsx`.
