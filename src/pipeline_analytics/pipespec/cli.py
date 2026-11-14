"""Command-line access to the PipeSpec Library.

Examples::

    pipespec grades --psl 2 --min-smys 450
    pipespec grade X70M
    pipespec sizes
    pipespec wt 24
    pipespec next-wt 24 11.2
    pipespec next-wt 24 11.2 --policy increment --step 0.1
    pipespec find 36 --min-grade X65 --process SAWH --max-wt 15.9
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from pipeline_analytics.pipespec.library import PipeSpecLibrary, WtPolicy, default_library


def _nps(text: str) -> str | float:
    try:
        return float(text)
    except ValueError:
        return text


def _table(rows: list[list[str]], header: list[str]) -> str:
    widths = [max(len(str(r[i])) for r in [header, *rows]) for i in range(len(header))]

    def line(r: list[str]) -> str:
        return "  ".join(str(c).ljust(w) for c, w in zip(r, widths, strict=True))

    return "\n".join([line(header), line(["-" * w for w in widths]), *map(line, rows)])


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="pipespec", description="Pipe specification library")
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("grades", help="list steel grades")
    g.add_argument("--standard")
    g.add_argument("--psl", type=int)
    g.add_argument("--min-smys", type=float)
    g.add_argument("--condition", choices=list("RNQM"))

    one = sub.add_parser("grade", help="show one grade")
    one.add_argument("designation")
    one.add_argument("--psl", type=int)

    sub.add_parser("sizes", help="list pipe sizes (ASME B36.10M)")

    w = sub.add_parser("wt", help="designated wall thicknesses for an NPS")
    w.add_argument("nps", type=_nps)

    n = sub.add_parser("next-wt", help="round a required wall thickness up")
    n.add_argument("nps", type=_nps)
    n.add_argument("required_mm", type=float)
    n.add_argument("--policy", choices=[x.value for x in WtPolicy], default="schedule")
    n.add_argument("--step", type=float, default=0.1, help="mill increment, mm")

    f = sub.add_parser("find", help="candidate pipes for an NPS")
    f.add_argument("nps", type=_nps)
    f.add_argument("--psl", type=int, default=2)
    f.add_argument("--min-grade")
    f.add_argument("--process")
    f.add_argument("--min-wt", type=float, default=0.0)
    f.add_argument("--max-wt", type=float)

    a = p.parse_args(argv)
    lib = default_library()
    try:
        out = _run(a, lib)
    except (KeyError, ValueError) as e:
        p.exit(2, f"pipespec: error: {e}\n")
    print(out)
    return 0


def _run(a: argparse.Namespace, lib: PipeSpecLibrary) -> str:
    if a.cmd == "grades":
        rows = [
            [
                g.standard,
                g.name,
                g.iso_name or "",
                f"{g.psl or ''}",
                ",".join(g.conditions),
                f"{g.smys:g}",
                f"{g.smys_max:g}" if g.smys_max else "",
                f"{g.smts:g}",
                f"{g.smts_max:g}" if g.smts_max else "",
                f"{g.yt_max or ''}",
            ]
            for g in lib.find_grades(a.standard, a.psl, a.min_smys, condition=a.condition)
        ]
        hdr = ["std", "grade", "iso", "psl", "cond", "smys", "smys_max", "smts", "smts_max", "y/t"]
        return _table(rows, hdr)
    if a.cmd == "grade":
        g = lib.grade(a.designation, psl=a.psl)
        return "\n".join(
            [
                g.label,
                "designations: " + ", ".join(g.designations()),
                f"SMYS {g.smys:g} MPa" + (f" (max {g.smys_max:g})" if g.smys_max else ""),
                f"SMTS {g.smts:g} MPa" + (f" (max {g.smts_max:g})" if g.smts_max else ""),
                f"Y/T max {g.yt_max}" if g.yt_max else "Y/T max: n/a",
                f"source: {g.standard} {g.ref}; verified: {g.verified}",
            ]
        )
    if a.cmd == "sizes":
        rows = [
            [s.nps, str(s.dn), f"{s.od_in:.3f}", f"{s.od_mm:.1f}", str(len(s.wall_thicknesses))]
            for s in lib.sizes
        ]
        return _table(rows, ["nps", "dn", "od_in", "od_mm", "n_wt"])
    if a.cmd == "wt":
        s = lib.size(a.nps)
        rows = [
            [
                f"{w.wt_in:.3f}",
                f"{w.wt_mm:.2f}",
                "/".join(w.designations),
                f"{s.weight_kg_m(w.wt_mm):.2f}",
            ]
            for w in s.wall_thicknesses
        ]
        return f"NPS {s.nps} (DN {s.dn}), OD {s.od_mm} mm\n" + _table(
            rows, ["wt_in", "wt_mm", "designation", "kg/m"]
        )
    if a.cmd == "next-wt":
        s = lib.size(a.nps)
        wt = lib.next_wall_thickness(s, a.required_mm, WtPolicy(a.policy), a.step)
        return f"{wt:g} mm ({s.weight_kg_m(wt):.2f} kg/m)"
    if a.cmd == "find":
        opts = lib.find(
            a.nps,
            psl=a.psl,
            min_grade=a.min_grade,
            process=a.process,
            min_wt_mm=a.min_wt,
            max_wt_mm=a.max_wt,
        )
        rows = [
            [
                o.process.code,
                o.grade.name,
                f"PSL{o.grade.psl}",
                f"{o.wt_mm:.2f}",
                f"{o.d_over_t:.0f}",
                f"{o.weight_kg_m:.1f}",
            ]
            for o in opts
        ]
        return f"{len(opts)} options\n" + _table(
            rows, ["process", "grade", "psl", "wt_mm", "D/t", "kg/m"]
        )
    raise AssertionError(a.cmd)  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
