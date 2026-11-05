"""Tools the Feasibility Bot can call. Each wraps the tested engine.

A tool is a name, a description, a Pydantic input model (its JSON schema is
what Claude sees) and a handler ``(study, validated_input) -> dict``. Inputs
are validated before a handler runs; a validation or engine error goes back to
Claude as an error result rather than raising.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from pipeline_analytics.bot.study import PSI_TO_PA, DesignBasis, Study, _In
from pipeline_analytics.core.jurisdiction import Jurisdiction, Service
from pipeline_analytics.economics.option import evaluate_option
from pipeline_analytics.mechanical.common import PipeSection
from pipeline_analytics.mechanical.pressure import LocationClass, pressure_design
from pipeline_analytics.optimiser import Alignment, DesignSpace, Leg, Town, optimise_design
from pipeline_analytics.pipespec import WtPolicy, default_library
from pipeline_analytics.reports.model import SensitivityRow

# ----------------------------------------------------------------- inputs


class LookupPipeInput(_In):
    nps: str | None = Field(default=None, description='pipe size, e.g. "24" or "1-1/4"')
    grade: str | None = Field(default=None, description='steel grade, e.g. "X70" or "L485M"')
    psl: Literal[1, 2] = 2


class WallThicknessInput(_In):
    nps: str
    grade: str = "X70"
    design_pressure_barg: float = Field(gt=0)
    service: Literal["gas", "liquid"] = "gas"
    location_class: Literal["1-1", "1-2", "2", "3", "4"] = "1-2"
    corrosion_allowance_mm: float = Field(default=0.0, ge=0)
    jurisdiction: Literal["IN", "BD"] = "IN"


class ReserveLifeInput(_In):
    remaining_reserves_tcf: float = Field(gt=0, description="remaining recoverable gas, Tcf")
    offtake_mmscfd: float = Field(gt=0, description="total production rate, MMSCFD")
    other_uses_mmscfd: float = Field(default=0.0, ge=0, description="other users of the same gas")


class TownIn(_In):
    name: str
    at_km: float = Field(ge=0, description="position along this leg")
    class3_km: float = Field(default=0.0, ge=0)
    class2_km: float = Field(default=0.0, ge=0)


class LegIn(_In):
    name: str
    length_km: float = Field(gt=0)
    wide_river_hdd_m: float = Field(default=0.0, ge=0)
    narrow_river_crossings: int = Field(default=0, ge=0)
    national_highways: int = Field(default=0, ge=0)
    state_highways: int = Field(default=0, ge=0)
    minor_roads: int = Field(default=0, ge=0)
    railways: int = Field(default=0, ge=0)
    wetland_fraction: float = Field(default=0.0, ge=0, le=1)
    urban_fraction: float = Field(default=0.0, ge=0, le=1)
    forest_fraction: float = Field(default=0.0, ge=0, le=1)
    elevation_m: float = 0.0
    towns: list[TownIn] = Field(default_factory=list)
    end_offtake_share: float = Field(
        default=0.0, ge=0, lt=1, description="share of inlet flow taken off at the leg end"
    )
    end_offtake_min_barg: float = 0.0
    end_name: str = ""


class DefineRouteInput(_In):
    name: str
    legs: list[LegIn] = Field(min_length=1)
    extra_terminals: int = Field(default=0, ge=0, description="e.g. a processing unit at the inlet")


class DesignSearchInput(_In):
    sizes: list[str] = Field(
        default_factory=lambda: ["16", "20", "24", "28", "30", "36"], description="NPS to try"
    )
    grades: list[str] = Field(default_factory=lambda: ["X65", "X70"])


class EvaluateDesignInput(_In):
    nps: str
    grade: str = "X70"
    design_pressure_barg: float | None = None


class SensitivityCase(_In):
    name: str
    operating_years: int | None = Field(default=None, ge=1, le=50)
    throughput_factor: float | None = Field(default=None, gt=0)
    discount_rate: float | None = Field(default=None, ge=0, lt=0.5)
    supply_pressure_end_barg: float | None = None
    capital_rate_factor: float | None = Field(default=None, gt=0)
    hdd_length_factor: float | None = Field(default=None, gt=0)


class SensitivityInput(_In):
    cases: list[SensitivityCase] = Field(min_length=1, max_length=12)
    sizes: list[str] = Field(default_factory=lambda: ["20", "24", "28", "30"])
    compare_nps: str | None = Field(
        default=None, description="also report this size's cost, e.g. a proposed design"
    )


class EmptyInput(_In):
    pass


def _both() -> list[Literal["docx", "xlsx"]]:
    return ["docx", "xlsx"]


class GenerateReportInput(_In):
    title: str = Field(min_length=3, description="report title, e.g. the project name")
    subtitle: str = ""
    compare_nps: str | None = Field(default=None, description="a proposed size to compare, e.g. 30")
    formats: list[Literal["docx", "xlsx"]] = Field(default_factory=lambda: _both())


# --------------------------------------------------------------- handlers


def lookup_pipe(study: Study, a: LookupPipeInput) -> dict[str, Any]:
    lib = default_library()
    out: dict[str, Any] = {}
    if a.grade:
        g = lib.grade(a.grade, psl=a.psl)
        out["grade"] = {
            "name": g.name, "iso_name": g.iso_name, "psl": g.psl,
            "smys_mpa": g.smys, "smts_mpa": g.smts, "smys_max_mpa": g.smys_max,
            "delivery_conditions": [c.value for c in g.conditions],
            "source": f"{g.standard} {g.ref}", "verified_against_standard": g.verified,
        }  # fmt: skip
    if a.nps:
        s = lib.size(a.nps)
        out["size"] = {
            "nps": s.nps, "dn": s.dn, "od_mm": s.od_mm,
            "designated_walls": [
                {"designation": "/".join(w.designations), "wt_mm": w.wt_mm,
                 "kg_per_m": round(s.weight_kg_m(w.wt_mm), 2)}
                for w in s.wall_thicknesses
            ],
            "processes": [p.code for p in lib.processes_for(s.od_mm)],
        }  # fmt: skip
    if not out:
        out["grades_psl2"] = [g.name for g in lib.find_grades(psl=2)]
        out["sizes"] = [s.nps for s in lib.sizes]
    return out


def wall_thickness(study: Study, a: WallThicknessInput) -> dict[str, Any]:
    lib = default_library()
    size = lib.size(a.nps)
    g = lib.grade(a.grade)
    probe = PipeSection(od_m=size.od_mm / 1000, wt_m=size.od_mm / 10000, smys_pa=g.smys * 1e6)
    service = Service.GAS if a.service == "gas" else Service.LIQUID
    r = pressure_design(
        probe, a.design_pressure_barg * 1e5, service, LocationClass(a.location_class),
        allowance=a.corrosion_allowance_mm / 1000, jurisdiction=Jurisdiction(a.jurisdiction),
    )  # fmt: skip
    req_mm = r.required_wt_m * 1000
    orderable = lib.next_wall_thickness(size, req_mm, WtPolicy.INCREMENT)
    return {
        "od_mm": size.od_mm,
        "smys_mpa": g.smys,
        "design_factor": r.design_factor,
        "temperature_factor": r.temperature_factor,
        "required_wall_mm": round(req_mm, 2),
        "orderable_wall_mm_0p1_step": orderable,
        "d_over_t_at_orderable": round(size.od_mm / orderable, 1),
        "basis": "ASME B31.8 841.1.1" if service is Service.GAS else "ASME B31.4 403.2.1",
    }


def reserve_life(study: Study, a: ReserveLifeInput) -> dict[str, Any]:
    total = a.offtake_mmscfd + a.other_uses_mmscfd
    return {
        "years_at_pipeline_rate_only": round(
            a.remaining_reserves_tcf * 1e6 / (a.offtake_mmscfd * 365), 1
        ),
        "years_including_other_uses": round(a.remaining_reserves_tcf * 1e6 / (total * 365), 1),
        "total_offtake_mmscfd": total,
    }


def set_design_basis(study: Study, a: DesignBasis) -> dict[str, Any]:
    study.basis = a
    defaults = {k: v for k, v in a.model_dump().items() if k not in a.model_fields_set}
    study.assumptions.update({f"default {k}": str(v) for k, v in defaults.items()})
    return {
        "stored": True,
        "throughput_profile": [round(q, 3) for q in study.throughput()],
        "throughput_unit": "MMSCMD" if a.service == "gas" else "MMTPA",
        "defaults_applied": defaults,
    }


def define_route(study: Study, a: DefineRouteInput) -> dict[str, Any]:
    legs = []
    for leg in a.legs:
        d = leg.model_dump()
        towns = tuple(Town(**t) for t in d.pop("towns"))
        legs.append(
            Leg(
                name=d["name"], length_km=d["length_km"], hdd_m=d["wide_river_hdd_m"],
                narrow_river_crossings=d["narrow_river_crossings"],
                national_highways=d["national_highways"], state_highways=d["state_highways"],
                minor_roads=d["minor_roads"], railways=d["railways"],
                marsh_fraction=d["wetland_fraction"], urban_fraction=d["urban_fraction"],
                forest_fraction=d["forest_fraction"], elevation_m=d["elevation_m"], towns=towns,
                end_offtake_share=d["end_offtake_share"],
                end_offtake_min_pa_g=d["end_offtake_min_barg"] * 1e5, end_name=d["end_name"],
            )
        )  # fmt: skip
    study.alignment = Alignment(name=a.name, legs=tuple(legs), extra_terminals=a.extra_terminals)
    al = study.alignment
    q = al.route_quantities()
    classes: dict[str, float] = {}
    for s in al.class_segments():
        classes[s.location_class.value] = classes.get(s.location_class.value, 0) + s.length_m / 1000
    return {
        "length_km": al.length_m / 1000,
        "wide_river_hdd_m": q.hdd_m,
        "wetland_km": round(al.wet_length_m / 1000, 1),
        "km_by_location_class": {k: round(v, 1) for k, v in classes.items()},
        "offtakes": [o.model_dump() for o in al.offtakes()],
        "terminals": q.terminals,
    }


def _space(study: Study, sizes: list[str], grades: list[str]) -> DesignSpace:
    b, _ = study.require()
    return DesignSpace(
        sizes=tuple(sizes), grades=tuple(grades),
        design_pressures_pa_g=tuple(p * 1e5 for p in b.design_pressures_barg),
    )  # fmt: skip


def run_design_search(study: Study, a: DesignSearchInput) -> dict[str, Any]:
    space = _space(study, a.sizes, a.grades)
    node = study.tree.add_group("design search", sizes=",".join(a.sizes))
    s = optimise_design(study.option_input(), space, tree=study.tree, parent=node)
    best_by_size: dict[str, Any] = {}
    for inp, res in s.evaluated:
        cur = best_by_size.get(res.nps)
        better = cur is None or (
            res.feasible and (not cur[1].feasible or res.lcc_pv < cur[1].lcc_pv)
        )
        if better:
            best_by_size[res.nps] = (inp, res)
    table = [
        study.summary(r, i)
        for _, (i, r) in sorted(best_by_size.items(), key=lambda kv: float(kv[0]))
    ]
    best = study.summary(s.best, s.best_input)
    study.last_best = best
    study.last_search = s
    return {
        "evaluations": len(s.evaluated),
        "best": best,
        "by_size": table,
        "disclosures": study.disclosures(),
    }


def evaluate_design(study: Study, a: EvaluateDesignInput) -> dict[str, Any]:
    inp = study.option_input(a.nps, a.grade, a.design_pressure_barg)
    res = evaluate_option(inp)
    study.tree.add_result(inp, res, label="single evaluation")
    out = study.summary(res, inp)
    if res.feasible and res.capex is not None:
        out["capex_by_group_usd_m"] = {k: study.usd_m(v) for k, v in res.capex.by_group().items()}
        out["stations_by_year"] = [h.stations for h in res.hydraulics]
        out["station_power_mw_by_year"] = [round(h.peak_power_kw / 1000, 2) for h in res.hydraulics]
        out["capex_accuracy"] = res.capex.accuracy_class
    out["disclosures"] = study.disclosures()
    return out


def run_sensitivity(study: Study, a: SensitivityInput) -> dict[str, Any]:
    from pipeline_analytics.cost.rates import UnitRates  # local: avoid import cycle in docs

    b, al = study.require()
    space = _space(study, a.sizes, ["X65", "X70"])
    rows = []
    sens_rows: list[SensitivityRow] = []
    for c in a.cases:
        basis = b
        upd: dict[str, Any] = {}
        if c.operating_years is not None:
            upd["operating_years"] = c.operating_years
        if c.discount_rate is not None:
            upd["discount_rate"] = c.discount_rate
        if c.supply_pressure_end_barg is not None:
            upd["supply_pressure_end_barg"] = c.supply_pressure_end_barg
        if upd:
            basis = DesignBasis.model_validate({**b.model_dump(), **upd})
        alignment = al
        if c.hdd_length_factor is not None:
            d = al.model_dump()
            for leg in d["legs"]:
                leg["hdd_m"] = min(leg["hdd_m"] * c.hdd_length_factor, leg["length_km"] * 1000)
            alignment = Alignment.model_validate(d)
        rates = study.rates
        if c.capital_rate_factor is not None:
            rates = _scale_capital(rates, c.capital_rate_factor)
            assert isinstance(rates, UnitRates)
        inp = study.option_input(
            basis=basis, alignment=alignment, rates=rates,
            throughput_factor=c.throughput_factor or 1.0,
        )  # fmt: skip
        node = study.tree.add_group(f"sensitivity: {c.name}")
        s = optimise_design(inp, space, tree=study.tree, parent=node)
        row: dict[str, Any] = {"case": c.name, "best": study.summary(s.best, s.best_input)}
        if a.compare_nps:
            cmp = [(i, r) for i, r in s.evaluated if r.nps == a.compare_nps and r.feasible]
            if cmp:
                _, r = min(cmp, key=lambda ir: ir[1].lcc_pv)
                row["compare"] = {
                    "nps": a.compare_nps,
                    "lifecycle_cost_pv_usd_m": study.usd_m(r.lcc_pv),
                    "premium_over_best_pct": round((r.lcc_pv / s.best.lcc_pv - 1) * 100, 1),
                }
        rows.append(row)
        cmp_row = row.get("compare", {})
        sens_rows.append(
            SensitivityRow(
                case=c.name,
                best=f'{s.best.nps}" {s.best.grade}',
                lcc=study.usd_m(s.best.lcc_pv),
                compare_lcc=cmp_row.get("lifecycle_cost_pv_usd_m"),
                compare_premium_pct=cmp_row.get("premium_over_best_pct"),
            )
        )
    study.sensitivity_rows = sens_rows
    return {"cases": rows, "disclosures": study.disclosures()}


def _scale_capital(rates: Any, k: float) -> Any:
    from pipeline_analytics.cost.rates import scale_capital

    return scale_capital(rates, k)


def _report_inputs(study: Study) -> list[tuple[str, str]]:
    b, al = study.require()
    unit = "MMSCFD" if b.service == "gas" else "MMTPA"
    plateau = b.throughput_plateau or b.throughput_start
    out = [
        ("Service / jurisdiction", f"{b.service}, {b.jurisdiction}"),
        ("Throughput", f"{b.throughput_start:g} -> {plateau:g} {unit} over {b.ramp_years} yr"),
        ("Concession", f"{b.construction_years} yr build + {b.operating_years} yr operation"),
        ("Discount rate (real)", f"{b.discount_rate:.1%}"),
        ("MAOP options", ", ".join(f"{p:g} barg" for p in b.design_pressures_barg)),
        ("Supply pressure", f"{b.supply_pressure_barg:g} barg"
         + (f" falling to {b.supply_pressure_end_barg:g} barg"
            if b.supply_pressure_end_barg is not None else "")),
        ("Minimum delivery pressure", f"{b.delivery_min_barg:g} barg"),
        ("Route", f"{al.name}: {al.length_m / 1000:g} km in {len(al.legs)} leg(s)"),
        ("Wide-river HDD", f"{al.route_quantities().hdd_m:,.0f} m"),
    ]  # fmt: skip
    for o in al.offtakes():
        out.append((f"Offtake {o.name or o.chainage_m / 1000}",
                    f"{o.share:.0%} of flow at >= {o.min_pressure_pa_g / 1e5:g} barg"))  # fmt: skip
    return out


def generate_report(study: Study, a: GenerateReportInput) -> dict[str, Any]:
    if study.last_search is None:
        raise ValueError("no design search yet: call run_design_search first")
    try:
        from pipeline_analytics.reports.docx import write_docx
        from pipeline_analytics.reports.model import Money, report_from_search
        from pipeline_analytics.reports.xlsx import write_xlsx
    except ImportError as e:  # pragma: no cover - extra not installed
        raise ValueError(f"report writers unavailable (install the reports extra): {e}") from e
    rate = study.basis.inr_per_usd if study.basis else 88.0
    money = Money(per_inr=1 / (rate * 1e6), unit_per_inr=1 / rate,
                  note=f"Converted at an assumed INR {rate:g} per US$.")  # fmt: skip
    report = report_from_search(
        study.last_search,
        title=a.title,
        subtitle=a.subtitle,
        money=money,
        compare_nps=a.compare_nps,
        inputs=_report_inputs(study),
        assumptions=[f"{k}: {v}" for k, v in study.assumptions.items()],
        disclosures=study.disclosures()[: 1 if study.rates.placeholder else 0],
        sensitivity=study.sensitivity_rows,
        scenarios_evaluated=len(study.tree.results()),
    )
    slug = "".join(ch if ch.isalnum() else "_" for ch in a.title).strip("_")[:60] or "study"
    files = {}
    if "docx" in a.formats:
        files["docx"] = str(write_docx(report, study.report_dir / f"{slug}.docx"))
    if "xlsx" in a.formats:
        files["xlsx"] = str(write_xlsx(report, study.report_dir / f"{slug}.xlsx"))
    study.last_report_files = files
    return {"files": files, "summary": report.summary, "disclosures": report.disclosures}


def get_study(study: Study, a: EmptyInput) -> dict[str, Any]:
    return {
        "has_design_basis": study.basis is not None,
        "design_basis": study.basis.model_dump() if study.basis else None,
        "route": None if study.alignment is None else {
            "name": study.alignment.name,
            "length_km": study.alignment.length_m / 1000,
            "legs": [leg.name for leg in study.alignment.legs],
        },
        "scenarios_evaluated": len(study.tree.results()),
        "last_best": study.last_best,
        "disclosures": study.disclosures(),
    }  # fmt: skip


# --------------------------------------------------------------- registry


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    model: type[BaseModel]
    handler: Callable[[Study, Any], dict[str, Any]]

    def definition(self) -> dict[str, Any]:
        schema = self.model.model_json_schema()
        return {"name": self.name, "description": self.description, "input_schema": schema}


TOOLS: tuple[Tool, ...] = (
    Tool(
        "lookup_pipe",
        (
            "Look up line-pipe data: a steel grade's strengths (API 5L / ISO 3183) and/or a pipe "
            "size's OD, designated wall thicknesses, weights and manufacturing processes (ASME "
            "B36.10M). With no arguments, lists available grades and sizes."
        ),
        LookupPipeInput,
        lookup_pipe,
    ),
    Tool(
        "pipe_wall_thickness",
        (
            "Required wall thickness for internal pressure (ASME B31.8 gas / B31.4 liquid, with "
            "OISD / PNGRB T4S or Bangladesh design factors) for a size, grade, design pressure "
            "and location class."
        ),
        WallThicknessInput,
        wall_thickness,
    ),
    Tool(
        "reserve_life",
        (
            "How many years a gas reserve supports a production rate, with and without other "
            "users of the same gas."
        ),
        ReserveLifeInput,
        reserve_life,
    ),
    Tool(
        "set_design_basis",
        (
            "Store the project design basis: service, throughput profile, concession, pressures, "
            "fluid, compressor driver, and whether to run soft-ground buried-pipe (ALA) checks. "
            "Returns the defaults that were applied so they can be stated as assumptions."
        ),
        DesignBasis,
        set_design_basis,
    ),
    Tool(
        "define_route",
        (
            "Store a fixed route described leg by leg: lengths, wide-river HDD length, narrow "
            "river and road/rail crossings, wetland/urban/forest shares, towns (location-class "
            "bands) and offtakes at leg ends. Returns length, location-class lengths and "
            "offtakes."
        ),
        DefineRouteInput,
        define_route,
    ),
    Tool(
        "run_design_search",
        (
            "Optimise the design on the stored route and basis: evaluates every size x grade x "
            "MAOP (wall per location class, yearly hydraulics with phased compression, CAPEX, "
            "OPEX, lifecycle cost). Returns the lowest lifecycle-cost design and the best result "
            "per size. Takes 1-3 minutes."
        ),
        DesignSearchInput,
        run_design_search,
    ),
    Tool(
        "evaluate_design",
        (
            "Evaluate one design in detail on the stored route and basis: CAPEX breakdown, "
            "stations and power by year, lifecycle cost, levelised cost and tariff."
        ),
        EvaluateDesignInput,
        evaluate_design,
    ),
    Tool(
        "run_sensitivity",
        (
            "Re-optimise the design under named sensitivity cases (operating years, throughput "
            "factor, discount rate, final supply pressure, capital-rate factor, HDD-length "
            "factor). Optionally compares a named size (e.g. a proposed design) in each case."
        ),
        SensitivityInput,
        run_sensitivity,
    ),
    Tool(
        "generate_report",
        (
            "Write the study report from the last design search (and sensitivity, if run): a "
            "Word report and an Excel workbook with inputs, method, results by size, CAPEX "
            "build-up, yearly cash flow and sensitivity. Returns the file paths."
        ),
        GenerateReportInput,
        generate_report,
    ),
    Tool(
        "get_study",
        (
            "Current study state: design basis, route, number of scenarios evaluated, last best "
            "result, and the disclosures/assumptions to state."
        ),
        EmptyInput,
        get_study,
    ),
)
TOOL_BY_NAME = {t.name: t for t in TOOLS}


def run_tool(study: Study, name: str, raw_input: Any) -> tuple[str, bool]:
    """Validate and run a tool. Returns (JSON text, is_error)."""
    tool = TOOL_BY_NAME.get(name)
    if tool is None:
        return json.dumps({"error": f"unknown tool {name!r}"}), True
    try:
        parsed = tool.model.model_validate(raw_input)
    except ValidationError as e:
        return json.dumps(
            {"error": "invalid input", "details": e.errors(include_url=False)}, default=str
        ), True
    try:
        result = tool.handler(study, parsed)
    except (ValueError, KeyError) as e:
        return json.dumps({"error": str(e)}), True
    return json.dumps(result, default=str), False


__all__ = ["PSI_TO_PA", "TOOLS", "TOOL_BY_NAME", "Tool", "run_tool"]
