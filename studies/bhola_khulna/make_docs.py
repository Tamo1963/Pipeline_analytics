"""Word report and Excel workbook for the Bhola - Barishal - Khulna test case.

    uv run python studies/bhola_khulna/make_docs.py --workers 4

Re-runs the route A (note) design search for full detail and takes the
sensitivity rows from results/summary.json (run run_study.py first).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from typing import Any

from pipeline_analytics.optimiser import DesignSpace, optimise_design
from pipeline_analytics.reports import Money, SensitivityRow, report_from_search
from pipeline_analytics.reports.docx import write_docx
from pipeline_analytics.reports.xlsx import write_xlsx

HERE = Path(__file__).parent


def _study() -> Any:
    spec = importlib.util.spec_from_file_location("bhola_run_study", HERE / "run_study.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()
    rs = _study()
    case = rs.load_case()
    summary = json.loads((HERE / "results" / "summary.json").read_text())
    ds, p = case["design_space"], case["pressures"]
    al = rs.build_alignment(case["alignments"]["A_note"])
    inp = al.option_input(rs.base_input(case))
    space = DesignSpace(
        sizes=tuple(ds["sizes"]),
        grades=tuple(ds["grades"]),
        design_pressures_pa_g=tuple(x * rs.PSI_TO_PA for x in p["design_options_psig"]),
    )
    search = optimise_design(inp, space, workers=args.workers)
    res = summary["reserves"]
    sens = [
        SensitivityRow(
            case=s["case"],
            best=f'{s["best_nps"]}" {s["best_grade"]} @ {s["best_maop_psig"]} psig',
            lcc=s["best_lcc_usd_m"],
            compare_lcc=s["lcc_30in_usd_m"],
            compare_premium_pct=s["30in_premium_pct"],
        )
        for s in summary["sensitivity"]
    ]
    g, c = case["gas"], case["concession"]
    report = report_from_search(
        search,
        title="Bhola - Barishal - Khulna Gas Pipeline",
        subtitle="Screening optimisation of pipe diameter (route via Laharhat, 173 km)",
        money=Money(
            per_inr=1 / (case["study"]["inr_per_usd"] * 1e6),
            unit_per_inr=1 / case["study"]["inr_per_usd"],
            note=f"Converted at an assumed INR {case['study']['inr_per_usd']:g} per US$.",
        ),
        target_return=c["target_return"],
        compare_nps="30",
        inputs=[
            (
                "Throughput",
                f"{g['throughput_mmscfd']} MMSCFD for {c['operating_years']} years "
                "(client note; TBS / GTCL)",
            ),
            (
                "Route",
                "Bhola junction - Laharhat - Barishal (63 km) - Jhalokathi - Pirojpur - "
                "Bagerhat - Khulna (110 km)",
            ),
            (
                "Barishal offtake",
                f"{p['barishal_share']:.0%} of flow at >= {p['barishal_min_barg']} barg",
            ),
            ("Khulna delivery", f">= {p['khulna_min_barg']} barg"),
            ("Supply pressure", f"{p['supply_start_barg']} falling to {p['supply_end_barg']} barg"),
            ("MAOP options", " / ".join(f"{x} psig" for x in p["design_options_psig"])),
            (
                "Concession",
                f"BOO, {c['construction_years']} yr construction, "
                f"{c['discount_rate']:.0%} real discount rate",
            ),
            (
                "Remaining recoverable gas",
                f"{res['remaining_recoverable_tcf']} Tcf (Bapex via TBS): "
                f"{res['years_at_pipeline_rate_only']} yr at 180 MMSCFD, "
                f"{res['years_if_local_use_continues']} yr if island use continues",
            ),
        ],
        assumptions=[
            "River HDD 6 km (leg 1) and 3 km (leg 2); wetland 40 % / 30 %; towns as Class 3 bands "
            "(all engineering assumptions, see case.yaml).",
            "Deltaic soft clay with the water table at the surface: ALA buried-pipe checks and "
            "concrete weight coating on wet lengths.",
            "Gas-turbine compressors; fuel gas valued at INR 25/scm.",
            'Note vs public GTCL proposal: 173 km (30") vs 205 km (24"/30"); the note\'s '
            "reserve figure in 'trillion' cubic feet should read billion cubic feet.",
        ],
        disclosures=[
            "Placeholder India 2026 unit rates used as a regional proxy; a calibration "
            f"against GTCL's Tk 4,500 crore budget implies capital rates "
            f"x{summary['calibration']['capital_rate_factor']} (the optimum does not change)."
        ],
        sensitivity=sens,
        scenarios_evaluated=summary["evaluations"],
        next_steps=[
            "Confirm design flow and its duration (grid transit, upside reserves) with Petrobangla / GTCL.",
            "Obtain the Bapex field pressure forecast; it sets when compression is needed.",
            "Survey river crossings (CEGIS / ILF erosion data) and select crossing methods.",
            "Replace placeholder rates with Bangladesh rates and re-run.",
            "Confirm offtake split and delivery pressures at Barishal and Khulna.",
        ],
    )
    out = HERE / "reports"
    print(write_docx(report, out / "Bhola_Khulna_Screening.docx"))
    print(write_xlsx(report, out / "Bhola_Khulna_Screening.xlsx"))


if __name__ == "__main__":
    main()
