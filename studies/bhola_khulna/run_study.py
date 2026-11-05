"""Run the Bhola - Barishal - Khulna screening optimisation (Phase 5 test case).

    uv run python studies/bhola_khulna/run_study.py            # full study
    uv run python studies/bhola_khulna/run_study.py --quick    # reduced design space

Writes results/summary.json, results/scenarios.json and REPORT.md next to this file.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path
from typing import Any

import yaml

from pipeline_analytics.core.jurisdiction import Service
from pipeline_analytics.cost import UnitRates, crore, placeholder_rates
from pipeline_analytics.economics import Concession, OptionInput, OptionResult
from pipeline_analytics.hydraulics import Gas, GasStationInput, Profile
from pipeline_analytics.mechanical import SegmentInput, Soil, SoilType
from pipeline_analytics.optimiser import (
    Alignment,
    DesignSpace,
    Leg,
    ScenarioTree,
    Town,
    optimise_design,
)

HERE = Path(__file__).parent
MMSCF_TO_MMSCM = 0.0283168
PSI_TO_PA = 6894.757


def load_case(path: Path = HERE / "case.yaml") -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(path.read_text())
    return data


def build_alignment(spec: dict[str, Any]) -> Alignment:
    legs = []
    for leg in spec["legs"]:
        leg = dict(leg)
        towns = tuple(Town(**t) for t in leg.pop("towns", []))
        if "end_offtake_min_barg" in leg:
            leg["end_offtake_min_pa_g"] = leg.pop("end_offtake_min_barg") * 1e5
        legs.append(Leg(**leg, towns=towns))
    return Alignment(
        name=spec["description"], legs=tuple(legs), extra_terminals=spec.get("extra_terminals", 0)
    )


def supply_profile(start_barg: float, end_barg: float, years: int) -> tuple[float, ...]:
    if years == 1:
        return (start_barg * 1e5,)
    return tuple(
        (start_barg + (end_barg - start_barg) * t / (years - 1)) * 1e5 for t in range(years)
    )


def base_input(case: dict[str, Any], **over: Any) -> OptionInput:
    g, p, c, b = case["gas"], case["pressures"], case["concession"], case["burial"]
    years = over.pop("operating_years", c["operating_years"])
    mmscfd = over.pop("throughput_mmscfd", g["throughput_mmscfd"])
    supply_end = over.pop("supply_end_barg", p["supply_end_barg"])
    rate = over.pop("discount_rate", c["discount_rate"])
    soil = Soil(
        soil_type=SoilType.SOFT_CLAY,
        unit_weight_n_m3=b["unit_weight_kn_m3"] * 1e3,
        cohesion_pa=b["cohesion_kpa"] * 1e3,
        below_water_table=True,
    )
    data = {
        "service": Service.GAS,
        "nps": "30",
        "grade": "X70",
        "design_pressure_pa_g": p["design_options_psig"][0] * PSI_TO_PA,
        "profile": Profile.flat(1000),
        "throughput": (mmscfd * MMSCF_TO_MMSCM,) * years,
        "load_factor": 0.9,
        "gas": Gas(
            specific_gravity=g["specific_gravity"], flowing_temperature_c=g["flowing_temperature_c"]
        ),
        "gas_stations": GasStationInput(
            supply_pressure_pa_g=p["supply_start_barg"] * 1e5,
            delivery_min_pa_g=p["khulna_min_barg"] * 1e5,
            min_suction_pa_g=p["compressor_min_suction_barg"] * 1e5,
            max_ratio=p["compressor_max_ratio"],
        ),
        "supply_pressure_by_year": supply_profile(p["supply_start_barg"], supply_end, years),
        "burial": SegmentInput(
            service=Service.GAS,
            design_pressure_pa=1e6,
            cover_m=b["cover_m"],
            soil=soil,
            modulus_of_soil_reaction_pa=b["modulus_of_soil_reaction_mpa"] * 1e6,
            water_above_crown_m=b["cover_m"],
        ),
        "concession": Concession(
            construction_years=c["construction_years"],
            capex_phasing=tuple(c["capex_phasing"]),
            operating_years=years,
            discount_rate=rate,
        ),
    }
    data.update(over)
    return OptionInput.model_validate(data)


def scale_capital_rates(rates: UnitRates, k: float, hdd: float = 1.0) -> UnitRates:
    """All capital unit rates x ``k`` (and HDD additionally x ``hdd``); OPEX rates unchanged."""
    d = rates.model_dump()
    d["line_pipe"]["base_rate_per_tonne"] *= k
    d["coating_per_m2"] = {c: v * k for c, v in d["coating_per_m2"].items()}
    d["concrete_coating_per_m3"] *= k
    d["laying_per_inch_km"] *= k
    cr = d["crossings"]
    cr["hdd_per_inch_m"] *= k * hdd
    cr["open_cut_river_per_inch_m"] *= k
    cr["per_crossing"] = {c: v * k for c, v in cr["per_crossing"].items()}
    for f in (
        "compressor_fixed",
        "compressor_per_kw",
        "pump_fixed",
        "pump_per_kw",
        "terminal_each",
        "sectionalising_valve_each",
        "scraper_station_each",
    ):
        d["stations"][f] *= k
    d["per_km"] = {c: v * k for c, v in d["per_km"].items()}
    d["land"]["rou_compensation_per_ha"] = {
        c: v * k for c, v in d["land"]["rou_compensation_per_ha"].items()
    }
    d["land"]["permanent_land_per_ha"] *= k
    return UnitRates.model_validate(d)


def usd_m(inr: float, case: dict[str, Any]) -> float:
    return float(inr / case["study"]["inr_per_usd"] / 1e6)


def summarise(res: OptionResult, inp: OptionInput, case: dict[str, Any]) -> dict[str, Any]:
    cf = res.cashflow
    assert cf is not None and res.capex is not None
    tariff = cf.tariff_for_return(case["concession"]["target_return"])
    first = next((h.operating_year for h in res.hydraulics if h.stations), None)
    return {
        "nps": res.nps,
        "grade": res.grade,
        "maop_psig": round(inp.design_pressure_pa_g / PSI_TO_PA),
        "walls_mm": {w.location_class.value: round(w.wt_m * 1000, 1) for w in res.walls},
        "wall_governed_by": sorted({w.governing for w in res.walls}),
        "pipe_tonnes": round(res.pipe_tonnes),
        "concrete_m3": round(res.concrete_coating_m3),
        "capex_usd_m": round(usd_m(res.capex.total, case), 1),
        "pv_capex_usd_m": round(usd_m(res.pv_capex, case), 1),
        "pv_opex_usd_m": round(usd_m(res.pv_opex, case), 1),
        "lcc_usd_m": round(usd_m(res.lcc_pv, case), 1),
        "lcc_inr_cr": round(crore(res.lcc_pv), 1),
        "max_stations": max(h.stations for h in res.hydraulics),
        "first_compression_year": first,
        "peak_compression_mw": round(max(h.peak_power_kw for h in res.hydraulics) / 1000, 2),
        "levelised_usd_per_mmbtu": round(
            res.levelised_cost_per_mmbtu / case["study"]["inr_per_usd"], 3
        ),
        "tariff_12pct_usd_per_mmbtu": round(
            tariff * res.scm_per_mmbtu / case["study"]["inr_per_usd"], 3
        ),
    }


def per_size(search: Any) -> dict[str, tuple[OptionInput, OptionResult]]:
    """Best (grade, MAOP) for each size."""
    out: dict[str, tuple[OptionInput, OptionResult]] = {}
    for inp, res in search.evaluated:
        if not res.feasible:
            out.setdefault(res.nps, (inp, res))
            continue
        cur = out.get(res.nps)
        if cur is None or not cur[1].feasible or res.lcc_pv < cur[1].lcc_pv:
            out[res.nps] = (inp, res)
    return out


def reserve_check(case: dict[str, Any]) -> dict[str, float]:
    r = case["reserves"]
    remaining = r["recoverable_tcf"] - r["produced_to_oct_2024_tcf"]
    q = case["gas"]["throughput_mmscfd"]
    local = r["island_local_use_mmscfd"]
    return {
        "remaining_recoverable_tcf": round(remaining, 3),
        "years_at_pipeline_rate_only": round(remaining * 1e6 / (q * 365), 1),
        "years_if_local_use_continues": round(remaining * 1e6 / ((q + local) * 365), 1),
        "upside_years_at_pipeline_rate": round(
            (r["upside_total_tcf"] - r["produced_to_oct_2024_tcf"]) * 1e6 / ((q + local) * 365), 1
        ),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()
    case = load_case()
    ds = case["design_space"]
    p = case["pressures"]
    sizes = ("20", "24", "30") if args.quick else tuple(ds["sizes"])
    grades = ("X70",) if args.quick else tuple(ds["grades"])
    space = DesignSpace(
        sizes=sizes, grades=grades,
        design_pressures_pa_g=tuple(x * PSI_TO_PA for x in p["design_options_psig"]),
    )  # fmt: skip
    small = DesignSpace(sizes=("20", "24", "26", "28", "30"), grades=("X65", "X70"),
                        design_pressures_pa_g=space.design_pressures_pa_g)  # fmt: skip
    if args.quick:
        small = space
    tree = ScenarioTree()
    out: dict[str, Any] = {"reserves": reserve_check(case), "alignments": {}, "sensitivity": []}
    t0 = time.perf_counter()

    for key, spec in case["alignments"].items():
        al = build_alignment(spec)
        inp = al.option_input(base_input(case))
        node = tree.add_group(f"alignment {key}", length_km=al.length_m / 1000)
        s = optimise_design(inp, space, tree=tree, parent=node, label=key, workers=args.workers)
        sizes_tbl = {}
        for nps, (i2, r2) in sorted(per_size(s).items(), key=lambda kv: float(kv[0])):
            sizes_tbl[nps] = summarise(r2, i2, case) if r2.feasible else {"infeasible": r2.issues}
        out["alignments"][key] = {
            "description": spec["description"],
            "length_km": al.length_m / 1000,
            "hdd_m": al.route_quantities().hdd_m,
            "wet_length_km": round(al.wet_length_m / 1000, 1),
            "best": summarise(s.best, s.best_input, case),
            "by_size": sizes_tbl,
        }

    # Calibration: scale capital rates so GTCL's design (96 km 24" + 109 km 30") matches its
    # published Tk 4,500 crore. Our engine has one diameter per option, so GTCL's cost is
    # approximated by the length-weighted mix of route B at 24" and at 30".
    b_sizes = out["alignments"]["B_gtcl"]["by_size"]
    mix_usd = (96 * b_sizes["24"]["capex_usd_m"] + 109 * b_sizes["30"]["capex_usd_m"]) / 205
    gtcl_usd = 4500e7 / case["study"]["bdt_per_usd"] / 1e6
    k_cal = gtcl_usd / mix_usd
    out["calibration"] = {
        "gtcl_budget_usd_m": round(gtcl_usd, 1),
        "model_gtcl_design_usd_m": round(mix_usd, 1),
        "capital_rate_factor": round(k_cal, 2),
    }
    high_capex = scale_capital_rates(placeholder_rates(), k_cal)
    hdd5 = scale_capital_rates(placeholder_rates(), 1.0, hdd=5.0)

    # Sensitivities on the note's alignment: the design is re-optimised each time
    reserves = out["reserves"]
    cases: list[tuple[str, dict[str, Any], dict[str, Any]]] = [
        ("Base (19 yr, 180 mmscfd)", {}, {}),
        ("Island local use continues: 13 yr", {"operating_years": round(
            reserves["years_if_local_use_continues"])}, {}),
        ("Upside reserves: 25 yr", {"operating_years": 25}, {}),
        ("Throughput 260 mmscfd", {"throughput_mmscfd": 260}, {}),
        ("Discount rate 12 %", {"discount_rate": 0.12}, {}),
        ("Supply holds at 62 barg", {"supply_end_barg": 62.0}, {}),
        ("Supply falls to 25 barg", {"supply_end_barg": 25.0}, {}),
        ("River HDD x2", {}, {"hdd_factor": 2.0}),
        ("No Barishal offtake", {}, {"barishal_share": 0.0}),
        ("HDD unit cost x5", {"rates": hdd5}, {}),
        (f"Capital rates x{k_cal:.1f} (calibrated to GTCL budget)", {"rates": high_capex}, {}),
    ]  # fmt: skip
    for name, over, route_over in cases:
        spec = json.loads(json.dumps(case["alignments"]["A_note"]))
        if "hdd_factor" in route_over:
            for leg in spec["legs"]:
                leg["hdd_m"] *= route_over["hdd_factor"]
        if "barishal_share" in route_over:
            spec["legs"][0]["end_offtake_share"] = route_over["barishal_share"]
        al = build_alignment(spec)
        inp = al.option_input(base_input(case, **over))
        node = tree.add_group(f"sensitivity: {name}")
        s = optimise_design(inp, small, tree=tree, parent=node, label=name, workers=args.workers)
        table = per_size(s)
        r30 = table.get("30")
        penalty = (
            (r30[1].lcc_pv - s.best.lcc_pv) / s.best.lcc_pv * 100
            if r30 and r30[1].feasible
            else math.nan
        )
        out["sensitivity"].append(
            {
                "case": name,
                "best_nps": s.best.nps,
                "best_grade": s.best.grade,
                "best_maop_psig": round(s.best_input.design_pressure_pa_g / PSI_TO_PA),
                "best_lcc_usd_m": round(usd_m(s.best.lcc_pv, case), 1),
                "lcc_30in_usd_m": round(usd_m(r30[1].lcc_pv, case), 1)
                if r30 and r30[1].feasible
                else None,
                "30in_premium_pct": round(penalty, 1),
                "best_tariff_usd_per_mmbtu": summarise(s.best, s.best_input, case)[
                    "tariff_12pct_usd_per_mmbtu"
                ],
            }
        )
    out["runtime_s"] = round(time.perf_counter() - t0, 1)
    out["evaluations"] = len(tree.results())
    res_dir = HERE / "results"
    res_dir.mkdir(exist_ok=True)
    (res_dir / "summary.json").write_text(json.dumps(out, indent=1, default=str))
    (res_dir / "scenarios.json").write_text(tree.to_json())
    print(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    main()
