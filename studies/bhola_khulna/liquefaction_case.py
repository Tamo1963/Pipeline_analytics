"""Bhola - Khulna: earthquake liquefaction and lateral spreading at river banks (what-if).

    uv run python studies/bhola_khulna/liquefaction_case.py

The route crosses the Tetulia (Bhola - Barishal) and the Sandhya, Kocha /
Baleshwar and Rupsha (Barishal - Khulna) by HDD, through young, saturated
deltaic sands and silts. Lateral spreading of the banks in an earthquake can
push the shallow pipe beyond each HDD exit. This case estimates, for assumed
ground and earthquake conditions:

* whether the deltaic sands liquefy (Idriss & Boulanger 2008);
* how far the banks spread (Youd et al. 2002);
* how far back from each bank toe the HDD must surface for the pipe to stay
  within ALA strain limits (pipe-soil FE), and what that adds to cost;
* whether the empty pipe floats in liquefied flood plain.

NO BOREHOLE DATA: the soil profile is a generic young deltaic bank, and the
earthquake scenarios are assumptions anchored to BNBC 2020 seismic Zone 1
(Z = 0.12) for Barishal and Khulna. Writes results/liquefaction_case.json and
LIQUEFACTION_CASE.md.
"""

from __future__ import annotations

import importlib.util
import json
import time
from pathlib import Path
from typing import Any

from pipeline_analytics.cost.boq import with_indirects
from pipeline_analytics.economics.option import evaluate_option
from pipeline_analytics.geotech import Earthquake, deltaic_profile, lateral_spread, triggering
from pipeline_analytics.geotech.pipe import bank_spread_check, flotation_check
from pipeline_analytics.mechanical import pipe_from_library
from pipeline_analytics.mechanical.soil import Coating, Soil, SoilType, soil_springs

HERE = Path(__file__).parent
MAIN_CROSSINGS = 4  # Tetulia; Sandhya, Kocha/Baleshwar, Rupsha (ASSUMED main HDDs)
SETBACKS = (0.0, 25.0, 50.0, 100.0, 150.0, 200.0, 300.0, 400.0)
SOIL_FACTOR = 1.5  # ASSUMED amplification for soft deltaic soil
EARTHQUAKES = (
    ("BNBC Zone 1, M 7.5 at 30 km", Earthquake(magnitude=7.5, pga_g=0.12 * SOIL_FACTOR,
                                                distance_km=30)),
    ("BNBC Zone 1, M 7.5 at 60 km", Earthquake(magnitude=7.5, pga_g=0.12 * SOIL_FACTOR,
                                                distance_km=60)),
    ("BNBC Zone 1, M 7.5 at 100 km", Earthquake(magnitude=7.5, pga_g=0.12 * SOIL_FACTOR,
                                                 distance_km=100)),
    ("Distant great earthquake, M 8.0 at 200 km", Earthquake(magnitude=8.0, pga_g=0.08,
                                                             distance_km=200)),
)  # fmt: skip
BANK_HEIGHTS = (3.0, 5.0, 8.0)


def _study() -> Any:
    spec = importlib.util.spec_from_file_location("bhola_run_study", HERE / "run_study.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> None:
    t0 = time.perf_counter()
    rs = _study()
    case = rs.load_case()
    al = rs.build_alignment(case["alignments"]["A_note"])
    base = al.option_input(rs.base_input(case))
    b = case["burial"]
    soil = Soil(soil_type=SoilType.SOFT_CLAY, unit_weight_n_m3=b["unit_weight_kn_m3"] * 1e3,
                cohesion_pa=b["cohesion_kpa"] * 1e3, below_water_table=True)  # fmt: skip
    designs = []
    for nps, grade in (("24", "X65"), ("30", "X70")):
        inp = base.model_validate({**base.model_dump(), "nps": nps, "grade": grade})
        res = evaluate_option(inp)
        wt = float(min(w.wt_m for w in res.walls))
        designs.append((nps, grade, wt, inp, res))
    profile = deltaic_profile()
    out: dict[str, Any] = {"profile": profile.model_dump(), "soil_factor": SOIL_FACTOR,
                           "scenarios": []}  # fmt: skip
    for label, eq in EARTHQUAKES:
        t = triggering(profile, eq)
        sc: dict[str, Any] = {
            "earthquake": label, "pga_g": eq.pga_g, "lpi": round(t.lpi, 1),
            "liquefied_thickness_m": t.liquefied_thickness_m,
            "layers": [{"depth_m": r.depth_m, "fs": None if r.factor_of_safety == float("inf")
                        else round(r.factor_of_safety, 2)} for r in t.layers],
            "banks": [],
        }  # fmt: skip
        for h in BANK_HEIGHTS:
            ls = lateral_spread(t, free_face_height_m=h)
            bank: dict[str, Any] = {"bank_height_m": h,
                                    "spread_at_toe_m": round(ls.max_displacement_m, 2),
                                    "extent_m": round(ls.extent_m), "designs": []}  # fmt: skip
            for nps, grade, wt, inp, _res in designs:
                if ls.max_displacement_m <= 0:
                    continue
                pipe = pipe_from_library(nps, wt * 1000, grade)
                sp = soil_springs(pipe.od_m, b["cover_m"] + pipe.od_m / 2, soil,
                                  Coating.POLYETHYLENE)  # fmt: skip
                c = bank_spread_check(
                    pipe, sp, ls, grade=grade, pressure_pa=inp.design_pressure_pa_g,
                    setbacks_m=SETBACKS,
                )  # fmt: skip
                need = c.required_setback_m
                extra = None
                if need is not None:
                    # both banks of each main crossing, if exits were at the toes
                    length = 2 * MAIN_CROSSINGS * need
                    od_in = pipe.od_m / 0.0254
                    direct = length * od_in * inp.rates.crossings.hdd_per_inch_m
                    extra = rs.usd_m(with_indirects(direct, 0.0, inp.rates), case)
                bank["designs"].append({
                    "design": f'{nps}" {grade} ({wt * 1000:.1f} mm)',
                    "utilisation_by_setback": {int(x.setback_m): round(x.utilisation, 2)
                                               for x in c.cases},
                    "required_setback_m": need,
                    "extra_hdd_capex_usd_m": None if extra is None else round(extra, 2),
                })  # fmt: skip
            sc["banks"].append(bank)
        out["scenarios"].append(sc)
    out["flotation"] = []
    for nps, grade, wt, _inp, _res in designs:
        f = flotation_check(pipe_from_library(nps, wt * 1000, grade))
        pw = pipe_from_library(nps, wt * 1000, grade)
        w = flotation_check(pw, liquefied_density_kg_m3=1000)
        out["flotation"].append({
            "design": f'{nps}" {grade}',
            "uplift_liquefied_kn_m": round(f.net_uplift_n_per_m / 1e3, 1),
            "concrete_liquefied_mm": round(f.required_concrete_mm),
            "concrete_water_mm": round(w.required_concrete_mm),
        })  # fmt: skip
    out["base_lcc_usd_m"] = round(rs.usd_m(designs[0][4].lcc_pv, case), 1)
    out["runtime_s"] = round(time.perf_counter() - t0, 1)
    (HERE / "results").mkdir(exist_ok=True)
    (HERE / "results" / "liquefaction_case.json").write_text(json.dumps(out, indent=1))
    (HERE / "LIQUEFACTION_CASE.md").write_text(markdown(out))
    print(json.dumps({k: v for k, v in out.items() if k != "profile"}, indent=1))


def _dash(v: Any) -> str:
    return "-" if v is None else f"{v}"


def markdown(d: dict[str, Any]) -> str:
    rows = []
    for sc in d["scenarios"]:
        for bk in sc["banks"]:
            if not bk["designs"]:
                rows.append(f'| {sc["earthquake"]} | {sc["lpi"]} | {bk["bank_height_m"]:g} | '
                            f'{bk["spread_at_toe_m"]} | - | no spread | - | - |')  # fmt: skip
            for ds in bk["designs"]:
                need = ds["required_setback_m"]
                rows.append(
                    f"| {sc['earthquake']} | {sc['lpi']} | {bk['bank_height_m']:g} | "
                    f"{bk['spread_at_toe_m']} | {bk['extent_m']} | {ds['design']} | "
                    f"{'> 400' if need is None else f'{need:g}'} | "
                    f"{_dash(ds['extra_hdd_capex_usd_m'])} |"
                )
    flo = "\n".join(
        f"| {f['design']} | {f['uplift_liquefied_kn_m']} | {f['concrete_liquefied_mm']} | "
        f"{f['concrete_water_mm']} |"
        for f in d["flotation"]
    )
    layers = "\n".join(
        f"| {lay['top_m']:g}-{lay['bottom_m']:g} | {lay['description']} | {lay['n60']:g} | "
        f"{lay['fines_pct']:g} | {lay['d50_mm'] or '-'} |"
        for lay in d["profile"]["layers"]
    )
    return f"""# Bhola - Khulna: liquefaction and lateral spreading at river banks (what-if)

> **No borehole data.** The soil profile is a generic young deltaic river bank and the
> earthquakes are assumptions anchored to BNBC 2020 seismic Zone 1 (Z = 0.12 g) for
> Barishal and Khulna, amplified x{d["soil_factor"]:g} for soft soil (assumed). Use this
> to see which questions matter, not as a design result.

**Method.**
* Triggering: SPT-based, Idriss & Boulanger (2008), giving the liquefaction potential
  index (LPI).
* Lateral spread behind each bank: Youd, Hansen & Bartlett (2002).
* Pipe: the pipe-soil FE model with ALA springs in the study's soft clay backfill, using
  the Class 1 wall at 1000 psig. For each design it finds the smallest HDD exit setback
  from the bank toe that keeps strain within the ALA limits (normal operability).
* Flotation: liquefied soil treated as a heavy fluid (1850 kg/m3), per ALA 2001.

**Assumed profile** (water table 1 m):

| Depth m | Soil | N60 | Fines % | D50 mm |
|---|---|---|---|---|
{layers}

## Lateral spread and HDD setback

| Earthquake | LPI | Bank m | Spread at toe m | Extent m | Design | Setback m | Extra HDD US$M |
|---|---|---|---|---|---|---|---|
{chr(10).join(rows)}

Extra HDD CAPEX assumes the base design surfaces at the bank toes and extends both
banks of the {MAIN_CROSSINGS} main crossings by the setback, at the placeholder HDD rate.
For comparison, the base lifecycle cost of 24" X65 is US$ {d["base_lcc_usd_m"]}M.

## Flotation in liquefied flood plain (empty pipe)

| Design | Net uplift kN/m | Concrete coating to hold down mm (liquefied) | (water only) mm |
|---|---|---|---|
{flo}

## Reading the results

* **Liquefaction is likely in the loose deltaic sands** at the BNBC Zone 1 design level
  (LPI about 10, high). The distant great earthquake does not trigger it with this
  profile.
* **Lateral spread depends strongly on the source distance**, which the code zoning does
  not give. At 30 km a 5 m bank spreads about 2 m and the movement reaches about 500 m
  inland; at 100 km the spread is much smaller. A site-specific seismic hazard study
  (deaggregation by magnitude and distance) is therefore the first data need.
* **The remedy is HDD length, not pipe size.** Surfacing the HDDs some tens of metres
  back from the bank toes keeps the pipe within the strain limits. That costs well under
  1 % of lifecycle cost. The thicker-walled 30" needs less setback, but that does not
  outweigh its higher lifecycle cost.
* **Flotation.** An empty pipe in liquefied soil needs far more ballast than in water.
  Where open-cut trench crosses liquefiable flood plain, use set-on weights or screw
  anchors, or keep the pipe below the liquefiable layer, rather than the concrete
  coating sized for water.
* **Data needed:**
  * SPT or CPT boreholes at each main crossing and in the flood plain (N, fines, D50,
    and PI of the crust);
  * bank survey (height, slope, distance to the river channel);
  * a site-specific seismic hazard study for the route.

Runtime {d["runtime_s"]} s. Reproduce:
`uv run python studies/bhola_khulna/liquefaction_case.py`.
"""


if __name__ == "__main__":
    main()
