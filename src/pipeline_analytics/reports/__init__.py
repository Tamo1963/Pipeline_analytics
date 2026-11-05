"""Study reports: a shared report model, a Word report and an Excel workbook.

Needs the ``reports`` extra (python-docx, openpyxl, pillow).
"""

from pipeline_analytics.reports.model import (
    CapexLine,
    Money,
    OptionRow,
    SensitivityRow,
    StudyReport,
    YearRow,
    best_per_size,
    report_from_options,
    report_from_search,
)

__all__ = [
    "CapexLine",
    "Money",
    "OptionRow",
    "SensitivityRow",
    "StudyReport",
    "YearRow",
    "best_per_size",
    "report_from_options",
    "report_from_search",
    "write_docx",
    "write_xlsx",
]


def __getattr__(name: str) -> object:
    # Writers import their libraries lazily so the model works without the extra.
    if name == "write_docx":
        from pipeline_analytics.reports.docx import write_docx

        return write_docx
    if name == "write_xlsx":
        from pipeline_analytics.reports.xlsx import write_xlsx

        return write_xlsx
    raise AttributeError(name)
