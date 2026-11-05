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
from pipeline_analytics.optimiser.fixed_alignment import LegHazard
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


class LegHazardIn(_In):
    name: str
    kind: Literal["fault", "landslide", "lateral_spread", "subsidence"]
    at_km: float = Field(ge=0, description="position within the leg")
    displacement_m: float = Field(gt=0, description="design fault slip or ground displacement")
    crossing_angle_deg: float = Field(
        default=90.0, ge=0, lt=360,
        description="fault: angle from the pipe to the fault strike; zone: angle from the pipe "
        "to the ground movement (0 = along the pipe, 90 = across it)",
    )  # fmt: skip
    dip_deg: float = Field(default=90.0, gt=0, le=90)
    rake_deg: float = Field(default=0.0, ge=-180, le=180,
                            description="0 left-lateral, 180 right-lateral, 90 reverse, "
                            "-90 normal")  # fmt: skip
    width_m: float = Field(default=50.0, gt=0, description="zone length along the pipe")


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
    geohazards: list[LegHazardIn] = Field(
        default_factory=list,
        description="mapped faults or landslide / lateral-spread / subsidence zones on this leg",
    )


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


class RangeIn(_In):
    low: float = Field(gt=0)
    mode: float = Field(gt=0)
    high: float = Field(gt=0)


class ThroughputCaseIn(_In):
    name: str
    probability: float = Field(gt=0, le=1)
    factor: float = Field(gt=0)


class RiskAnalysisInput(_In):
    sizes: list[str] | None = Field(
        default=None,
        description="sizes to compare (best grade/MAOP of each from the last design search); "
        "default: the feasible sizes from that search, cheapest first, up to 5",
    )
    rate_ranges: (
        dict[
            Literal["line_pipe", "construction", "stations", "other_capex", "energy", "o_and_m"],
            RangeIn,
        ]
        | None
    ) = Field(
        default=None,
        description="triangular factor ranges on unit-rate groups; groups not given keep "
        "the screening defaults",
    )
    throughput_cases: list[ThroughputCaseIn] | None = Field(
        default=None,
        description="weighted throughput scenarios (probabilities sum to 1); default "
        "low 0.8 (25%), base 1.0 (50%), high 1.2 (25%)",
    )
    samples: int = Field(default=10_000, ge=1_000, le=200_000)
    seed: int = 1


class GroundMovementInput(_In):
    nps: str
    wall_mm: float = Field(gt=0)
    grade: str = "X65"
    design_pressure_barg: float = Field(default=0.0, ge=0)
    cover_m: float = Field(default=1.2, gt=0, description="cover to the pipe crown")
    soil: Literal["dense_sand", "loose_sand", "stiff_clay", "soft_clay"] = "dense_sand"
    unit_weight_kn_m3: float = Field(default=18.0, gt=0)
    friction_angle_deg: float = Field(default=36.0, ge=0, le=45, description="0 for clay")
    cohesion_kpa: float = Field(default=0.0, ge=0, description="undrained strength for clay")
    scenario: Literal["fault", "transverse_pgd", "settlement", "differential_settlement",
                      "longitudinal_pgd"]  # fmt: skip
    displacement_m: float = Field(gt=0, description="fault slip or ground displacement")
    crossing_angle_deg: float = Field(
        default=90.0, gt=0, lt=180, description="fault: angle between pipe and strike"
    )
    dip_deg: float = Field(default=90.0, gt=0, le=90)
    rake_deg: float = Field(default=0.0, ge=-180, le=180,
                            description="0 strike-slip, 90 reverse, -90 normal")  # fmt: skip
    zone_width_m: float = Field(default=50.0, gt=0, description="PGD zone / trough / block")
    pattern: Literal["cosine", "block"] = "cosine"
    performance_level: Literal["normal_operability", "pressure_integrity"] = "normal_operability"
    sweep_crossing_angles: bool = Field(
        default=False, description="fault only: also find the crossing angle with lowest strain"
    )


class SptLayerIn(_In):
    top_m: float = Field(ge=0)
    bottom_m: float = Field(gt=0)
    n60: float = Field(ge=0, description="SPT N corrected to 60 % hammer energy")
    fines_pct: float = Field(default=5.0, ge=0, le=100)
    d50_mm: float | None = Field(default=None, gt=0)
    unit_weight_kn_m3: float = Field(default=18.0, gt=0)
    plasticity_index: float | None = Field(default=None, ge=0)


class LiquefactionInput(_In):
    layers: list[SptLayerIn] | None = Field(
        default=None,
        description="SPT profile from the ground surface; omit to use a generic young "
        "deltaic river-bank profile (an assumption to state)",
    )
    water_table_m: float = Field(default=1.0, ge=0)
    magnitude: float = Field(ge=5, le=9.5)
    pga_g: float = Field(gt=0, le=2, description="peak ground acceleration at the surface")
    distance_km: float = Field(ge=0, description="to the nearest edge of the seismic source")
    bank_height_m: float | None = Field(default=5.0, gt=0, description="free face (river bank)")
    ground_slope_pct: float | None = Field(default=None, gt=0, description="instead of a bank")
    nps: str = "24"
    wall_mm: float = Field(default=9.5, gt=0)
    grade: str = "X65"
    design_pressure_barg: float = Field(default=0.0, ge=0)
    cover_m: float = Field(default=1.2, gt=0)
    backfill: Literal["soft_clay", "stiff_clay", "loose_sand", "dense_sand"] = "soft_clay"
    backfill_strength_kpa: float = Field(default=15.0, ge=0, description="undrained (clay)")
    skew_deg: float = Field(default=0.0, ge=0, lt=80, description="pipe to the bank normal")
    hdd_transition_m: float = Field(default=0.0, ge=0)


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


def ground_movement_check(study: Study, a: GroundMovementInput) -> dict[str, Any]:
    from pipeline_analytics.mechanical import pipe_from_library
    from pipeline_analytics.mechanical.pgd import (
        analyse_pgd,
        crossing_angle_sweep,
        fault_movement,
        longitudinal_pgd,
        settlement,
        transverse_pgd,
    )
    from pipeline_analytics.mechanical.seismic import PerformanceLevel
    from pipeline_analytics.mechanical.soil import Coating, Soil, SoilType, soil_springs

    pipe = pipe_from_library(a.nps, a.wall_mm, a.grade)
    soil = Soil(
        soil_type=SoilType(a.soil),
        unit_weight_n_m3=a.unit_weight_kn_m3 * 1e3,
        friction_angle_deg=a.friction_angle_deg,
        cohesion_pa=a.cohesion_kpa * 1e3,
    )
    springs = soil_springs(pipe.od_m, a.cover_m + pipe.od_m / 2, soil, Coating.POLYETHYLENE)
    d = a.displacement_m
    mv = {
        "fault": lambda: fault_movement(
            d, a.crossing_angle_deg, dip_deg=a.dip_deg, rake_deg=a.rake_deg
        ),
        "transverse_pgd": lambda: transverse_pgd(d, a.zone_width_m, a.pattern),
        "settlement": lambda: settlement(d, a.zone_width_m),
        "differential_settlement": lambda: settlement(d, pattern="step"),
        "longitudinal_pgd": lambda: longitudinal_pgd(d, a.zone_width_m),
    }[a.scenario]()
    level = PerformanceLevel(a.performance_level)
    kw: dict[str, Any] = {"grade": a.grade, "pressure": a.design_pressure_barg * 1e5,
                          "level": level}  # fmt: skip
    r = analyse_pgd(pipe, springs, mv, **kw)
    lim = r.movement_at_limit_m
    out: dict[str, Any] = {
        "scenario": mv.label,
        "result": "PASS" if r.passed else "FAIL",
        "converged": r.fe.converged,
        "max_tensile_strain_pct": round(r.fe.max_tensile_strain * 100, 3),
        "max_compressive_strain_pct": round(r.fe.max_compressive_strain * 100, 3),
        "tensile_limit_pct": round(r.tensile_limit * 100, 3),
        "compressive_limit_pct": round(r.compressive_limit * 100, 3),
        "strain_utilisation": round(r.utilisation, 2),
        "movement_at_first_strain_limit_m": None if lim is None else round(lim, 2),
        "soil_springs_kn_per_m": {
            "axial": round(springs.axial.max_force_n_per_m / 1e3, 1),
            "lateral": round(springs.lateral.max_force_n_per_m / 1e3, 1),
            "uplift": round(springs.uplift.max_force_n_per_m / 1e3, 1),
            "bearing": round(springs.bearing.max_force_n_per_m / 1e3, 1),
        },
        "notes": list(r.notes),
        "method": "Pipe-soil FE (large displacement beam on ALA 2001 springs, Ramberg-Osgood "
        "steel); strain limits per ALA 2001. Screening: local buckling is judged by the "
        "strain limit, not modelled.",
    }
    if a.scenario == "fault" and a.sweep_crossing_angles:
        sw = crossing_angle_sweep(pipe, springs, d, (15, 30, 45, 60, 75, 90, 105, 120),
                                  dip_deg=a.dip_deg, rake_deg=a.rake_deg, **kw)  # fmt: skip
        out["crossing_angle_sweep"] = [
            {"crossing_angle_deg": c.crossing_angle_deg,
             "strain_utilisation": round(c.utilisation, 2) if c.converged else "not converged"}
            for c in sw.cases
        ]  # fmt: skip
        out["best_crossing_angle_deg"] = sw.best_angle_deg
    return out


def liquefaction_check(study: Study, a: LiquefactionInput) -> dict[str, Any]:
    from pipeline_analytics.geotech import (
        Earthquake,
        SptLayer,
        SptProfile,
        deltaic_profile,
        lateral_spread,
        triggering,
    )
    from pipeline_analytics.geotech.pipe import bank_spread_check, flotation_check
    from pipeline_analytics.mechanical import pipe_from_library
    from pipeline_analytics.mechanical.soil import Coating, Soil, SoilType, soil_springs

    assumed = a.layers is None
    profile = (
        deltaic_profile()
        if a.layers is None
        else SptProfile(
            layers=tuple(SptLayer(**x.model_dump()) for x in a.layers),
            water_table_m=a.water_table_m,
        )
    )
    eq = Earthquake(magnitude=a.magnitude, pga_g=a.pga_g, distance_km=a.distance_km)
    t = triggering(profile, eq)
    out: dict[str, Any] = {
        "profile": "ASSUMED generic deltaic river bank" if assumed else "as given",
        "layers": [
            {"depth_m": round(r.depth_m, 1), "n1_60cs": round(r.n1_60cs, 1),
             "csr": round(r.csr, 3), "crr": None if r.crr == float("inf") else round(r.crr, 3),
             "factor_of_safety": None if r.factor_of_safety == float("inf")
             else round(r.factor_of_safety, 2),
             "liquefies": r.liquefies}
            for r in t.layers
        ],
        "liquefaction_potential_index": round(t.lpi, 1),
        "liquefied_thickness_m": round(t.liquefied_thickness_m, 1),
        "method": "Idriss & Boulanger (2008) SPT triggering; Youd et al. (2002) lateral spread; "
        "pipe-soil FE (ALA springs) for the pipe; flotation per ALA 2001",
    }  # fmt: skip
    ls = lateral_spread(t, free_face_height_m=None if a.ground_slope_pct else a.bank_height_m,
                        slope_pct=a.ground_slope_pct)  # fmt: skip
    out["lateral_spread"] = {
        "mode": ls.mode,
        "displacement_at_bank_m": round(ls.max_displacement_m, 2),
        "extent_m": None if ls.extent_m == float("inf") else round(ls.extent_m),
        "displacement_vs_distance_m": [
            [round(p.distance_m), round(p.displacement_m, 2)] for p in ls.profile[::6]
        ],
        "t15_m": round(ls.t15_m, 1), "f15_pct": round(ls.f15_pct), "d50_15_mm": ls.d50_15_mm,
        "warnings": list(ls.warnings),
    }  # fmt: skip
    pipe = pipe_from_library(a.nps, a.wall_mm, a.grade)
    soil_type = SoilType(a.backfill)
    soil = (Soil(soil_type=soil_type, unit_weight_n_m3=17.5e3,
                 cohesion_pa=a.backfill_strength_kpa * 1e3, below_water_table=True)
            if not soil_type.granular else
            Soil(soil_type=soil_type, unit_weight_n_m3=19e3, friction_angle_deg=32,
                 below_water_table=True))  # fmt: skip
    springs = soil_springs(pipe.od_m, a.cover_m + pipe.od_m / 2, soil, Coating.POLYETHYLENE)
    if ls.mode == "free_face" and ls.max_displacement_m > 0:
        c = bank_spread_check(pipe, springs, ls, grade=a.grade,
                              pressure_pa=a.design_pressure_barg * 1e5, skew_deg=a.skew_deg,
                              transition_m=a.hdd_transition_m)  # fmt: skip
        out["hdd_setback"] = {
            "required_setback_from_bank_toe_m": c.required_setback_m,
            "cases": [{"setback_m": x.setback_m, "displacement_m": round(x.displacement_m, 2),
                       "strain_utilisation": None if x.utilisation == float("inf")
                       else round(x.utilisation, 2), "passes": x.passed} for x in c.cases],
        }  # fmt: skip
    if t.liquefied_thickness_m > 0:
        f = flotation_check(pipe)
        out["flotation_in_liquefied_soil"] = {
            "floats_when_empty": f.floats,
            "net_uplift_kn_per_m": round(f.net_uplift_n_per_m / 1e3, 2),
            "concrete_coating_to_hold_down_mm": round(f.required_concrete_mm),
        }
    return out


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
        hazards = tuple(LegHazard(**h) for h in d.pop("geohazards"))
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
                geohazards=hazards,
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
        "geohazards": [
            {"name": c.hazard, "kind": c.kind, "chainage_km": round(c.chainage_m / 1000, 2)}
            for c in al.geohazard_crossings()
        ],
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
    out: dict[str, Any] = {
        "evaluations": len(s.evaluated),
        "best": best,
        "by_size": table,
        "disclosures": study.disclosures(),
    }
    geo = _geohazards(study, s.best_input, s.best)
    if geo is not None:
        out["geohazards"] = geo
    return out


def _geohazards(study: Study, inp: Any, res: Any) -> dict[str, Any] | None:
    """FE strain checks at the route's faults / ground-movement zones for one design."""
    from pipeline_analytics.optimiser.search import design_geohazards

    _, al = study.require()
    a = design_geohazards(al.geohazard_crossings(), al.location_class_at, inp, res)
    if a is None:
        return None
    if a.unmitigated:
        study.assumptions["geohazards"] = "a crossing needs realignment or special design"
    return {
        "checks": [
            {
                "hazard": c.crossing.hazard,
                "kind": c.crossing.kind,
                "chainage_km": round(c.crossing.chainage_m / 1000, 2),
                "wall_mm": c.wall_mm,
                "max_tensile_strain_pct": round(c.max_tensile_strain * 100, 3),
                "max_compressive_strain_pct": round(c.max_compressive_strain * 100, 3),
                "strain_utilisation": round(c.utilisation, 2),
                "result": "PASS" if c.passed else "FAIL",
                "mitigation": c.mitigation or None,
                "mitigation_cost_usd_m": round(study.usd_m(c.mitigation_cost), 2),
                "best_crossing_angle_deg": c.best_crossing_angle_deg,
            }
            for c in a.checks
        ],
        "all_safe_after_mitigation": a.passed,
        "mitigation_cost_usd_m": round(study.usd_m(a.mitigation_cost), 2),
        "basis": f"Pipe-soil FE on ALA springs; soil {a.basis.soil.soil_type.value}, cover "
        f"{a.basis.cover_m:g} m (from the design basis if ALA checks are on, else assumed); "
        "mitigation cost is not in the lifecycle cost above.",
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
    geo = _geohazards(study, inp, res)
    if geo is not None:
        out["geohazards"] = geo
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


def run_risk_analysis(study: Study, a: RiskAnalysisInput) -> dict[str, Any]:
    from pipeline_analytics.economics.risk import (
        RiskInputs,
        ThroughputScenario,
        Triangular,
        default_groups,
        monte_carlo,
    )
    from pipeline_analytics.reports.model import RiskRow

    s = study.last_search
    if s is None:
        raise ValueError("no design search yet: call run_design_search first")
    best: dict[str, Any] = {}
    for inp, res in s.evaluated:
        if res.feasible and (res.nps not in best or res.lcc_pv < best[res.nps][1].lcc_pv):
            best[res.nps] = (inp, res)
    if a.sizes:
        missing = [n for n in a.sizes if n not in best]
        if missing:
            raise ValueError(f"no feasible design in the last search for sizes {missing}")
        chosen = [best[n] for n in a.sizes]
    else:
        chosen = sorted(best.values(), key=lambda ir: ir[1].lcc_pv)[:5]
    groups = default_groups()
    for g, r in (a.rate_ranges or {}).items():
        groups[g] = Triangular(low=r.low, mode=r.mode, high=r.high)
    kw: dict[str, Any] = {"groups": groups, "samples": a.samples, "seed": a.seed}
    if a.throughput_cases:
        kw["scenarios"] = [
            ThroughputScenario(name=c.name, probability=c.probability, factor=c.factor)
            for c in a.throughput_cases
        ]
    inputs = RiskInputs(**kw)
    per_unit = [res.scm_per_mmbtu if res.throughput_unit == "scm" else 1.0 for _, res in chosen]
    mc = monte_carlo([i for i, _ in chosen], inputs, per_unit=per_unit)
    rows = [
        RiskRow(
            design=d.label,
            p10=study.usd_m(d.lcc_p10),
            p50=study.usd_m(d.lcc_p50),
            p90=study.usd_m(d.lcc_p90),
            deterministic=study.usd_m(d.deterministic_lcc),
            prob_cheapest=d.prob_cheapest,
            prob_infeasible=d.prob_infeasible,
        )
        for d in mc.designs
    ]
    from pipeline_analytics.reports.model import risk_note_for

    study.risk_rows = rows
    study.risk_note = risk_note_for(mc)
    study.assumptions["risk analysis"] = study.risk_note

    def num(v: float) -> float | str:
        return round(v, 1) if v != float("inf") else "infeasible"

    return {
        "designs": [
            {
                "design": r.design,
                "lifecycle_cost_p10_usd_m": num(r.p10),
                "lifecycle_cost_p50_usd_m": num(r.p50),
                "lifecycle_cost_p90_usd_m": num(r.p90),
                "deterministic_lifecycle_cost_usd_m": num(r.deterministic),
                "probability_cheapest_pct": round(r.prob_cheapest * 100, 1),
                "probability_infeasible_pct": round(r.prob_infeasible * 100, 1),
            }
            for r in rows
        ],
        "evaluations": mc.evaluations,
        "method_note": study.risk_note,
        "disclosures": study.disclosures(),
    }


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
        risk=study.risk_rows,
        risk_note=study.risk_note,
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
        "ground_movement_check",
        (
            "Pipe strain from permanent ground movement with a pipe-soil finite-element model "
            "(ALA 2001 soil springs, large displacement, nonlinear steel): fault crossing "
            "(slip, crossing angle, dip, rake), transverse PGD (lateral spread / landslide), "
            "settlement trough, differential settlement, or longitudinal PGD. Returns peak "
            "tensile and compressive strain against ALA strain limits, the movement at which a "
            "limit is reached, and optionally the best fault-crossing angle. Takes seconds "
            "(about a minute with the angle sweep)."
        ),
        GroundMovementInput,
        ground_movement_check,
    ),
    Tool(
        "liquefaction_check",
        (
            "Earthquake liquefaction and lateral spreading at a river bank or slope: SPT-based "
            "triggering (Idriss & Boulanger 2008) layer by layer, liquefaction potential "
            "index, lateral spread displacement and extent (Youd et al. 2002), the HDD exit "
            "setback from the bank needed for the pipe to stay within ALA strain limits "
            "(pipe-soil FE), and pipe flotation in liquefied soil. Without SPT data it uses "
            "a generic deltaic profile, which must be stated as an assumption."
        ),
        LiquefactionInput,
        liquefaction_check,
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
            "bands), offtakes at leg ends, and geohazards (mapped faults, landslide / lateral-"
            "spread / subsidence zones). Returns length, location-class lengths, offtakes and "
            "geohazards; design search and evaluation then check pipe strain at each "
            "geohazard."
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
        "run_risk_analysis",
        (
            "Monte Carlo risk analysis on the last design search: samples unit-rate groups "
            "(line pipe, construction, stations, other CAPEX, energy, O&M) from triangular "
            "ranges and throughput from weighted scenarios. Returns lifecycle-cost P10/P50/P90 "
            "per size and the probability that each size is the cheapest. Run after "
            "run_design_search; the result is included in generate_report."
        ),
        RiskAnalysisInput,
        run_risk_analysis,
    ),
    Tool(
        "generate_report",
        (
            "Write the study report from the last design search (and sensitivity, if run): a "
            "Word report and an Excel workbook with inputs, method, results by size, CAPEX "
            "build-up, yearly cash flow, sensitivity and risk. Returns the file paths."
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
