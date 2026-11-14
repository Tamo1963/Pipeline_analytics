"""Project unit-rate management: a folder of named, validated rate sets.

Each rate set is one YAML file ``<root>/<name>.yaml`` holding a
:class:`~pipeline_analytics.cost.rates.UnitRates`. The library lists, loads
and saves sets (atomic writes), checks a set for gaps and outliers, compares
two sets line by line, and escalates a set to a new base year.

The default root is ``$PIPELINE_RATES_DIR`` or ``./rates``. The bundled
placeholder set is always available as ``placeholder``; it cannot be
overwritten.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict

from pipeline_analytics.core.storage import atomic_write_text, file_lock
from pipeline_analytics.cost.rates import (
    RATE_GROUPS,
    CrossingType,
    LandUse,
    Terrain,
    UnitRates,
    placeholder_rates,
    scale_rate_groups,
)

PLACEHOLDER = "placeholder"
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
COMMON_GRADES = ("B", "X42", "X52", "X60", "X65", "X70", "X80")
COMMON_PROCESSES = ("SMLS", "HFW", "SAWL", "SAWH")
OUTLIER_RATIO = 4.0
"""A rate more than this many times above/below the reference is flagged."""
STALE_YEARS = 3


class _M(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class RateSetInfo(_M):
    name: str
    path: str
    currency: str
    base_year: int
    placeholder: bool
    approved_by: str
    approved_on: date | None
    region: str


class Finding(_M):
    level: str  # "error" | "warning" | "info"
    field: str
    message: str


class RateChange(_M):
    field: str
    old: Any
    new: Any

    @property
    def ratio(self) -> float | None:
        if isinstance(self.old, int | float) and isinstance(self.new, int | float) and self.old:
            return float(self.new) / float(self.old)
        return None


def default_root() -> Path:
    return Path(os.environ.get("PIPELINE_RATES_DIR", "rates"))


def check_name(name: str) -> str:
    if not _NAME.match(name):
        raise ValueError(
            f"invalid rate-set name {name!r}: use letters, digits, '_', '-', '.' (max 64)"
        )
    return name


def to_yaml(rates: UnitRates) -> str:
    header = (
        "# PLACEHOLDER rates: screening and testing only\n"
        if rates.placeholder
        else f"# Unit-rate set: {rates.meta.name or rates.source}\n"
    )
    data = rates.model_dump(mode="json")
    return header + yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


class RateLibrary:
    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root) if root is not None else default_root()

    def path(self, name: str) -> Path:
        return self.root / f"{check_name(name)}.yaml"

    def names(self) -> list[str]:
        own = sorted(p.stem for p in self.root.glob("*.yaml")) if self.root.is_dir() else []
        return [PLACEHOLDER, *(n for n in own if n != PLACEHOLDER)]

    def list(self) -> list[RateSetInfo]:
        out = []
        for n in self.names():
            r = self.load(n)
            out.append(
                RateSetInfo(
                    name=n,
                    path="(bundled)" if n == PLACEHOLDER else str(self.path(n)),
                    currency=r.currency,
                    base_year=r.base_year,
                    placeholder=r.placeholder,
                    approved_by=r.meta.approved_by,
                    approved_on=r.meta.approved_on,
                    region=r.meta.region,
                )
            )
        return out

    def load(self, name_or_path: str | Path) -> UnitRates:
        """A set by name, or any YAML file path."""
        s = str(name_or_path)
        if s == PLACEHOLDER:
            return placeholder_rates()
        p = Path(s) if s.endswith((".yaml", ".yml")) else self.path(s)
        if not p.exists():
            raise FileNotFoundError(f"no rate set {s!r} (looked for {p})")
        return UnitRates.model_validate(yaml.safe_load(p.read_text(encoding="utf-8")))

    def save(self, name: str, rates: UnitRates, *, overwrite: bool = False) -> Path:
        if check_name(name) == PLACEHOLDER:
            raise ValueError("the bundled placeholder set cannot be overwritten")
        p = self.path(name)
        with file_lock(p):
            if p.exists() and not overwrite:
                raise FileExistsError(f"rate set {name!r} exists (pass overwrite=True)")
            UnitRates.model_validate(rates.model_dump())  # re-validate before writing
            return atomic_write_text(p, to_yaml(rates))

    def __iter__(self) -> Iterator[str]:
        return iter(self.names())


# ------------------------------------------------------------------ checks


def _flatten(d: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(d, dict):
        out: dict[str, Any] = {}
        for k, v in d.items():
            out.update(_flatten(v, f"{prefix}.{k}" if prefix else str(k)))
        return out
    return {prefix: d}


def validate_rates(
    rates: UnitRates, reference: UnitRates | None = None, today: date | None = None
) -> list[Finding]:
    """Gaps and outliers. The model already rejects wrong types and negatives."""
    today = today or date.today()
    out: list[Finding] = []

    def add(level: str, field: str, msg: str) -> None:
        out.append(Finding(level=level, field=field, message=msg))

    if rates.placeholder:
        add("warning", "placeholder", "placeholder rates: screening and testing only")
    elif not rates.meta.approved_by:
        add("warning", "meta.approved_by", "not marked as approved")
    if rates.meta.valid_until and rates.meta.valid_until < today:
        add("warning", "meta.valid_until", f"expired on {rates.meta.valid_until}")
    if today.year - rates.base_year > STALE_YEARS:
        add("warning", "base_year", f"base year {rates.base_year} is over {STALE_YEARS} years "
            "old: escalate before use")  # fmt: skip
    lp = rates.line_pipe
    for g in COMMON_GRADES:
        if g not in lp.grade_factor:
            add("info", f"line_pipe.grade_factor.{g}", "no factor: this grade cannot be priced")
    for pr in COMMON_PROCESSES:
        if pr not in lp.process_factor:
            add("info", f"line_pipe.process_factor.{pr}", "no factor: this process cannot be "
                "priced")  # fmt: skip
    if "3LPE" not in rates.coating_per_m2:
        add("error", "coating_per_m2.3LPE", "3LPE is the default coating and has no rate")
    for t in Terrain:
        if t not in rates.terrain_factor:
            add("error", f"terrain_factor.{t}", "missing: routes with this terrain fail")
    for c in CrossingType:
        if c not in rates.crossings.per_crossing:
            add("error", f"crossings.per_crossing.{c}", "missing")
    for lu in LandUse:
        if lu not in rates.land.rou_compensation_per_ha:
            add("error", f"land.rou_compensation_per_ha.{lu}", "missing")
    ind = rates.indirects
    total = ind.taxes + ind.engineering_pmc + ind.owners_cost
    if total > 0.6 or ind.contingency > 0.4:
        add("warning", "indirects", f"indirects unusually high (t+e+o {total:.0%}, "
            f"contingency {ind.contingency:.0%})")  # fmt: skip
    if reference is not None and reference.currency == rates.currency:
        ref = _flatten(reference.model_dump(mode="json"))
        for k, v in _flatten(rates.model_dump(mode="json")).items():
            r = ref.get(k)
            if (
                isinstance(v, int | float) and not isinstance(v, bool)
                and isinstance(r, int | float) and not isinstance(r, bool)
                and r > 0 and v > 0 and k != "base_year"
                and not (1 / OUTLIER_RATIO <= v / r <= OUTLIER_RATIO)
            ):  # fmt: skip
                add("warning", k, f"{v:g} is {v / r:.2g}x the reference ({r:g})")
    return out


def diff_rates(a: UnitRates, b: UnitRates) -> list[RateChange]:
    fa, fb = _flatten(a.model_dump(mode="json")), _flatten(b.model_dump(mode="json"))
    return [
        RateChange(field=k, old=fa.get(k), new=fb.get(k))
        for k in sorted(set(fa) | set(fb))
        if fa.get(k) != fb.get(k)
    ]


def escalate(
    rates: UnitRates,
    to_year: int,
    annual: float,
    group_annual: dict[str, float] | None = None,
    *,
    source_name: str = "",
) -> UnitRates:
    """Move a set to ``to_year`` at compound annual escalation (per group if given).

    Percentage items (indirects, insurance, station O&M fraction) are unchanged.
    """
    n = to_year - rates.base_year
    group_annual = group_annual or {}
    unknown = set(group_annual) - set(RATE_GROUPS)
    if unknown:
        raise KeyError(f"unknown rate groups: {sorted(unknown)}")
    if annual <= -1 or any(v <= -1 for v in group_annual.values()):
        raise ValueError("escalation rates must be above -100 %")
    factors = {g: (1 + group_annual.get(g, annual)) ** n for g in RATE_GROUPS}
    out = scale_rate_groups(rates, factors)
    rates_txt = ", ".join(f"{g} {group_annual[g]:+.1%}" for g in sorted(group_annual))
    note = (
        f"{source_name or rates.meta.name or rates.source} {rates.base_year} -> {to_year} at "
        f"{annual:+.1%}/yr" + (f" ({rates_txt})" if rates_txt else "")
    )
    d = out.model_dump()
    d["base_year"] = to_year
    d["meta"] = {**d["meta"], "escalated_from": note, "approved_by": "", "approved_on": None}
    return UnitRates.model_validate(d)


__all__ = [
    "PLACEHOLDER",
    "Finding",
    "RateChange",
    "RateLibrary",
    "RateSetInfo",
    "default_root",
    "diff_rates",
    "escalate",
    "to_yaml",
    "validate_rates",
]
