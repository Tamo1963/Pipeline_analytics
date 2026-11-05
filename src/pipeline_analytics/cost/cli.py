"""``pipeline-rates``: manage project unit-rate sets.

    pipeline-rates list
    pipeline-rates show NAME
    pipeline-rates validate NAME|FILE [--reference NAME]
    pipeline-rates diff A B
    pipeline-rates new NAME [--from SRC] [--name ...] [--region ...]
    pipeline-rates escalate SRC NEW --to-year 2028 --annual 0.05 [--group energy=0.08]
    pipeline-rates approve NAME --by "Cost engineer" [--on 2026-11-05] [--valid-until ...]

``--root`` (or ``$PIPELINE_RATES_DIR``) selects the folder, default ``./rates``.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

from pipeline_analytics.cost.rate_library import (
    PLACEHOLDER,
    RateLibrary,
    diff_rates,
    escalate,
    to_yaml,
    validate_rates,
)
from pipeline_analytics.cost.rates import UnitRates


def _group_rates(items: list[str]) -> dict[str, float]:
    out = {}
    for it in items:
        g, _, v = it.partition("=")
        if not v:
            raise SystemExit(f"--group expects NAME=RATE, got {it!r}")
        out[g.strip()] = float(v)
    return out


def _rebuild(r: UnitRates, **meta: object) -> UnitRates:
    d = r.model_dump()
    d["meta"] = {**d["meta"], **{k: v for k, v in meta.items() if v is not None}}
    return UnitRates.model_validate(d)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pipeline-rates", description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", default=None, help="rate-set folder (default $PIPELINE_RATES_DIR "
                    "or ./rates)")  # fmt: skip
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    p = sub.add_parser("show")
    p.add_argument("name")
    p = sub.add_parser("validate")
    p.add_argument("name")
    p.add_argument("--reference", default=PLACEHOLDER)
    p = sub.add_parser("diff")
    p.add_argument("a")
    p.add_argument("b")
    p = sub.add_parser("new")
    p.add_argument("name")
    p.add_argument("--from", dest="src", default=PLACEHOLDER)
    p.add_argument("--title", default=None, help="display name stored in meta.name")
    p.add_argument("--region", default=None)
    p.add_argument("--source", default=None, help="where the rates come from")
    p = sub.add_parser("escalate")
    p.add_argument("src")
    p.add_argument("new")
    p.add_argument("--to-year", type=int, required=True)
    p.add_argument("--annual", type=float, required=True, help="e.g. 0.05 for 5 %%/yr")
    p.add_argument("--group", action="append", default=[], help="GROUP=RATE override")
    p.add_argument("--overwrite", action="store_true")
    p = sub.add_parser("approve")
    p.add_argument("name")
    p.add_argument("--by", required=True)
    p.add_argument("--on", type=date.fromisoformat, default=None)
    p.add_argument("--valid-until", type=date.fromisoformat, default=None)
    a = ap.parse_args(argv)
    lib = RateLibrary(a.root)
    out = sys.stdout

    try:
        if a.cmd == "list":
            out.write(f"{'name':24} {'cur':4} {'year':5} {'status':12} {'approved by':20} region\n")
            for i in lib.list():
                status = "PLACEHOLDER" if i.placeholder else ("approved" if i.approved_by
                                                              else "draft")  # fmt: skip
                out.write(f"{i.name:24} {i.currency:4} {i.base_year:<5} {status:12} "
                          f"{i.approved_by:20} {i.region}\n")  # fmt: skip
        elif a.cmd == "show":
            out.write(to_yaml(lib.load(a.name)))
        elif a.cmd == "validate":
            ref = lib.load(a.reference) if a.reference else None
            findings = validate_rates(lib.load(a.name), reference=ref)
            for f in findings:
                out.write(f"{f.level.upper():8} {f.field}: {f.message}\n")
            errors = sum(f.level == "error" for f in findings)
            out.write(f"{len(findings)} finding(s), {errors} error(s)\n")
            return 1 if errors else 0
        elif a.cmd == "diff":
            changes = diff_rates(lib.load(a.a), lib.load(a.b))
            for c in changes:
                ratio = f"  (x{c.ratio:.3g})" if c.ratio is not None else ""
                out.write(f"{c.field}: {c.old} -> {c.new}{ratio}\n")
            out.write(f"{len(changes)} difference(s)\n")
        elif a.cmd == "new":
            src = lib.load(a.src)
            d = src.model_dump()
            # values copied from the placeholder stay flagged until replaced by hand
            if a.source:
                d["source"] = a.source
            r = _rebuild(UnitRates.model_validate(d), name=a.title or a.name, region=a.region,
                         approved_by="", approved_on=None)  # fmt: skip
            path = lib.save(a.name, r)
            out.write(f"created {path}: edit the rates, set placeholder: false when they are "
                      "real, then run 'pipeline-rates validate'\n")  # fmt: skip
        elif a.cmd == "escalate":
            r = escalate(lib.load(a.src), a.to_year, a.annual, _group_rates(a.group),
                         source_name=a.src)  # fmt: skip
            path = lib.save(a.new, r, overwrite=a.overwrite)
            out.write(f"wrote {path} ({r.meta.escalated_from}); approval cleared\n")
        elif a.cmd == "approve":
            r = _rebuild(lib.load(a.name), approved_by=a.by, approved_on=a.on or date.today(),
                         valid_until=a.valid_until)  # fmt: skip
            if r.placeholder:
                raise ValueError("placeholder rates cannot be approved")
            lib.save(a.name, r, overwrite=True)
            out.write(f"{a.name} approved by {a.by}\n")
    except (ValueError, KeyError, FileNotFoundError, FileExistsError) as e:
        sys.stderr.write(f"error: {e}\n")
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
