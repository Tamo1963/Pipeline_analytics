"""Report model: everything a study report shows, built from optimiser output.

The same model feeds the Word report, the Excel workbook and the app, so the
three never disagree. Money is converted once, here, by :class:`Money`.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from pipeline_analytics.economics.option import OptionInput, OptionResult
from pipeline_analytics.optimiser.search import DesignSearch


class _M(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", ser_json_inf_nan="constants")


class Money(_M):
    """How INR amounts from the engine are shown in the report."""

    label: str = "US$ M"
    per_inr: float = Field(default=1 / 88e6, gt=0, description="report units per INR")
    unit_label: str = "US$"
    unit_per_inr: float = Field(
        default=1 / 88.0, gt=0, description="for tariffs: report currency per INR"
    )
    note: str = "Converted at an assumed INR 88 per US$."

    def m(self, inr: float) -> float:
        return inr * self.per_inr

    @classmethod
    def inr_crore(cls) -> Money:
        return cls(label="INR crore", per_inr=1e-7, unit_label="INR", unit_per_inr=1.0, note="")


class OptionRow(_M):
    nps: str
    grade: str
    maop_barg: float
    feasible: bool
    issue: str = ""
    walls_mm: dict[str, float] = Field(default_factory=dict)
    wall_governed_by: list[str] = Field(default_factory=list)
    pipe_tonnes: float = 0.0
    capex: float = 0.0
    pv_capex: float = 0.0
    pv_opex: float = 0.0
    lcc: float = 0.0
    max_stations: int = 0
    first_station_year: int | None = None
    peak_power_mw: float = 0.0
    levelised: float = 0.0
    tariff: float = 0.0


class CapexLine(_M):
    group: str
    description: str
    quantity: float
    unit: str
    rate: float
    amount: float


class YearRow(_M):
    year: int
    phase: str
    throughput: float
    stations: int
    peak_power_mw: float
    capex: float
    opex: float
    energy: float
    net_cost: float


class SensitivityRow(_M):
    case: str
    best: str
    lcc: float
    compare_lcc: float | None = None
    compare_premium_pct: float | None = None


class StudyReport(_M):
    title: str
    subtitle: str = ""
    prepared_on: str = Field(default_factory=lambda: dt.date.today().isoformat())
    money: Money = Money()
    throughput_unit: str = ""
    tariff_unit: str = ""
    summary: list[str] = Field(default_factory=list)
    inputs: list[tuple[str, str]] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    disclosures: list[str] = Field(default_factory=list)
    method: list[str] = Field(default_factory=list)
    options: list[OptionRow] = Field(default_factory=list)
    best: OptionRow
    compare: OptionRow | None = None
    capex_lines: list[CapexLine] = Field(default_factory=list)
    capex_indirects: list[tuple[str, float]] = Field(default_factory=list)
    capex_total: float = 0.0
    capex_accuracy: str = ""
    years: list[YearRow] = Field(default_factory=list)
    sensitivity: list[SensitivityRow] = Field(default_factory=list)
    scenarios_evaluated: int = 0
    next_steps: list[str] = Field(default_factory=list)


METHOD = [
    "Pressure design to ASME B31.8 (gas) or B31.4 (liquids) with OISD / PNGRB T4S design factors, "
    "a D/t <= 96 handling limit and walls rounded up to a 0.1 mm mill step, per location class.",
    "Location classes from building density per ASME B31.8 840.2 (as adopted by PNGRB T4S / "
    "OISD-STD-226) where applicable.",
    "Buried-pipe checks per the American Lifelines Alliance Guidelines for the Design of Buried "
    "Steel Pipe where soil data was given; concrete weight coating sized for buoyancy on wet "
    "lengths.",
    "Steady-state hydraulics for every operating year; compressor or pump stations sited and "
    "phased in when first needed; energy from average flow, station sizing from peak flow.",
    "CAPEX from a bill of quantities and unit rates with taxes, engineering, owner's costs and "
    "contingency; OPEX from energy, O&M, manpower, in-line inspection and insurance.",
    "Lifecycle cost = present value of CAPEX + OPEX - residual value over the concession, in real "
    "terms with end-of-year discounting; tariff = constant charge for the target post-tax return.",
]


def _row(inp: OptionInput, res: OptionResult, money: Money, target_return: float) -> OptionRow:
    if not res.feasible or res.cashflow is None or res.capex is None:
        return OptionRow(
            nps=res.nps,
            grade=res.grade,
            maop_barg=inp.design_pressure_pa_g / 1e5,
            feasible=False,
            issue="; ".join(res.issues),
        )
    per_unit = res.scm_per_mmbtu if res.throughput_unit == "scm" else 1.0
    tariff = res.cashflow.tariff_for_return(target_return)
    return OptionRow(
        nps=res.nps,
        grade=res.grade,
        maop_barg=round(inp.design_pressure_pa_g / 1e5, 2),
        feasible=True,
        walls_mm={w.location_class.value: round(w.wt_m * 1000, 1) for w in res.walls},
        wall_governed_by=sorted({w.governing for w in res.walls}),
        pipe_tonnes=res.pipe_tonnes,
        capex=money.m(res.capex.total),
        pv_capex=money.m(res.pv_capex),
        pv_opex=money.m(res.pv_opex),
        lcc=money.m(res.lcc_pv),
        max_stations=max(h.stations for h in res.hydraulics),
        first_station_year=next((h.operating_year for h in res.hydraulics if h.stations), None),
        peak_power_mw=max(h.peak_power_kw for h in res.hydraulics) / 1000,
        levelised=res.levelised_cost * per_unit * money.unit_per_inr,
        tariff=tariff * per_unit * money.unit_per_inr,
    )


def best_per_size(
    evaluated: Iterable[tuple[OptionInput, OptionResult]],
) -> list[tuple[OptionInput, OptionResult]]:
    out: dict[str, tuple[OptionInput, OptionResult]] = {}
    for inp, res in evaluated:
        cur = out.get(res.nps)
        if cur is None or (res.feasible and (not cur[1].feasible or res.lcc_pv < cur[1].lcc_pv)):
            out[res.nps] = (inp, res)
    return [out[k] for k in sorted(out, key=float)]


def report_from_options(
    evaluated: Sequence[tuple[OptionInput, OptionResult]],
    **kwargs: Any,
) -> StudyReport:
    """Build a report from any list of evaluated options (the lowest-LCC one in detail)."""
    feasible = [(i, r) for i, r in evaluated if r.feasible]
    if not feasible:
        raise ValueError("no feasible option to report")
    best_input, best = min(feasible, key=lambda ir: ir[1].lcc_pv)
    search = DesignSearch(best_input, best, list(evaluated), None, None)  # type: ignore[arg-type]
    return report_from_search(search, **kwargs)


def report_from_search(
    search: DesignSearch,
    *,
    title: str,
    subtitle: str = "",
    money: Money | None = None,
    target_return: float = 0.12,
    compare_nps: str | None = None,
    inputs: Sequence[tuple[str, str]] = (),
    assumptions: Sequence[str] = (),
    disclosures: Sequence[str] = (),
    sensitivity: Sequence[SensitivityRow] = (),
    next_steps: Sequence[str] = (),
    scenarios_evaluated: int | None = None,
) -> StudyReport:
    """Build a report from an optimiser design search (best design in detail)."""
    money = money or Money()
    inp, res = search.best_input, search.best
    assert res.cashflow is not None and res.capex is not None
    rows = [_row(i, r, money, target_return) for i, r in best_per_size(search.evaluated)]
    best = _row(inp, res, money, target_return)
    compare = next((r for r in rows if r.nps == compare_nps and r.feasible), None)

    lines = [
        CapexLine(
            group=i.group,
            description=i.description,
            quantity=i.quantity,
            unit=i.unit,
            rate=i.rate * money.unit_per_inr,
            amount=money.m(i.amount),
        )
        for i in res.capex.items
    ]
    indirects = [
        ("Taxes", money.m(res.capex.taxes)),
        ("Engineering / PMC", money.m(res.capex.engineering)),
        ("Owner's costs", money.m(res.capex.owners_cost)),
        ("Contingency", money.m(res.capex.contingency)),
    ]
    conc = res.cashflow.concession
    years: list[YearRow] = []
    for y in res.cashflow.years:
        op = y.year - conc.construction_years
        h = res.hydraulics[op - 1] if y.operating else None
        o = res.opex[op - 1] if y.operating else None
        years.append(
            YearRow(
                year=y.year,
                phase="operation" if y.operating else "construction",
                throughput=y.throughput,
                stations=h.stations if h else 0,
                peak_power_mw=(h.peak_power_kw / 1000) if h else 0.0,
                capex=money.m(y.capex),
                opex=money.m(y.opex),
                energy=money.m(o.energy) if o else 0.0,
                net_cost=money.m(y.capex + y.opex - y.residual),
            )
        )
    tu = "scm per year" if res.throughput_unit == "scm" else "t per year"
    tariff_unit = (
        f"{money.unit_label}/MMBtu" if res.throughput_unit == "scm" else f"{money.unit_label}/t"
    )

    summary = [
        f'Lowest lifecycle cost: {best.nps}" {best.grade} at {best.maop_barg:g} barg MAOP, '
        f"{best.lcc:,.1f} {money.label} present value.",
        f"CAPEX {best.capex:,.1f} {money.label}; lifecycle cost split "
        f"{best.pv_capex:,.1f} PV CAPEX and {best.pv_opex:,.1f} PV OPEX.",
        f"Tariff for a {target_return:.0%} post-tax return: {best.tariff:,.3f} {tariff_unit}.",
    ]
    if best.first_station_year:
        summary.append(
            f"Compression or pumping first needed in operating year {best.first_station_year}; "
            f"peak station power {best.peak_power_mw:,.2f} MW."
        )
    else:
        summary.append("No compressor or pump station is needed over the concession.")
    if compare is not None and compare.nps != best.nps:
        summary.append(
            f'The {compare.nps}" option costs {compare.lcc:,.1f} {money.label}, '
            f"{(compare.lcc / best.lcc - 1) * 100:.1f} % more than the optimum."
        )
    if sensitivity:
        bests = sorted({s.best for s in sensitivity})
        cases = "case" if len(sensitivity) == 1 else "cases"
        summary.append(
            f"Across {len(sensitivity)} sensitivity {cases} the optimum is {' or '.join(bests)}."
        )

    return StudyReport(
        title=title,
        subtitle=subtitle,
        money=money,
        throughput_unit=tu,
        tariff_unit=tariff_unit,
        summary=summary,
        inputs=list(inputs),
        assumptions=list(assumptions),
        disclosures=list(disclosures)
        + ([res.capex.accuracy_class] if res.capex.placeholder_rates else []),
        method=METHOD,
        options=rows,
        best=best,
        compare=compare,
        capex_lines=lines,
        capex_indirects=indirects,
        capex_total=money.m(res.capex.total),
        capex_accuracy=res.capex.accuracy_class,
        years=years,
        sensitivity=list(sensitivity),
        scenarios_evaluated=scenarios_evaluated or len(search.evaluated),
        next_steps=list(next_steps)
        or [
            "Replace placeholder unit rates with project rates and re-run.",
            "Confirm design flow, supply-pressure forecast and delivery pressures with the client.",
            "Route survey: crossings, geotechnical and environmental data; confirm location "
            "classes.",
            "Detailed engineering and regulatory review before any investment decision.",
        ],
    )


def as_dict(report: StudyReport) -> dict[str, Any]:
    return report.model_dump(mode="json")
