"""Bhola - Khulna: HYPOTHETICAL fault crossing (what-if).

    uv run python studies/bhola_khulna/fault_case.py --workers 4

No mapped active fault crosses this corridor (the known active sources, e.g. the
Dauki, Madhupur and Jamuna faults and the Chittagong / Arakan belt, lie well to
the north and east). This case asks: if site investigation found one, would it
change the design? A strike-slip fault is placed on the Barishal - Khulna leg at
km 60 (rural, Class 1, between Pirojpur and Bagerhat). The soil is the study's
deltaic soft clay (case.yaml ``burial``).

Writes results/fault_case.json and FAULT_CASE.md.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import time
from pathlib import Path
from typing import Any

from pipeline_analytics.mechanical import pipe_from_library
from pipeline_analytics.mechanical.pgd import crossing_angle_sweep
from pipeline_analytics.mechanical.soil import soil_springs
from pipeline_analytics.optimiser import DesignSpace, optimise_design
from pipeline_analytics.optimiser.fixed_alignment import Alignment, LegHazard
from pipeline_analytics.optimiser.search import _basis_of, design_geohazards, mitigation_pv

HERE = Path(__file__).parent
LEG, AT_KM = 1, 60.0  # Barishal - Khulna leg, km 60
SLIPS = (0.5, 1.0, 2.0)
ANGLES = (30, 45, 60, 75, 90, 105, 120, 135, 150)


def _study() -> Any:
    spec = importlib.util.spec_from_file_location("bhola_run_study", HERE / "run_study.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def with_fault(al: Alignment, hazard: LegHazard) -> Alignment:
    d = al.model_dump()
    d["legs"][LEG]["geohazards"] = [hazard.model_dump()]
    return Alignment.model_validate(d)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--markdown-only", action="store_true",
                    help="rewrite FAULT_CASE.md from results/fault_case.json")  # fmt: skip
    args = ap.parse_args()
    if args.markdown_only:
        d = json.loads((HERE / "results" / "fault_case.json").read_text())
        (HERE / "FAULT_CASE.md").write_text(markdown(d))
        return
    rs = _study()
    case = rs.load_case()
    t0 = time.perf_counter()
    al = rs.build_alignment(case["alignments"]["A_note"])
    inp = al.option_input(rs.base_input(case))
    ds, p = case["design_space"], case["pressures"]
    space = DesignSpace(sizes=tuple(ds["sizes"]), grades=tuple(ds["grades"]),
                        design_pressures_pa_g=tuple(x * rs.PSI_TO_PA for x in
                                                    p["design_options_psig"]))  # fmt: skip
    search = optimise_design(inp, space, workers=args.workers)
    sizes = {n: ir for n, ir in rs.per_size(search).items() if ir[1].feasible}
    basis = _basis_of(inp)
    usd = lambda inr: round(rs.usd_m(inr, case), 2)  # noqa: E731

    def class1_wall(res: Any) -> float:
        return min(w.wt_m for w in res.walls)

    out: dict[str, Any] = {
        "location": f"leg {LEG + 1} km {AT_KM:g} (Class 1)",
        "soil": basis.model_dump(mode="json"),
        "sweeps": {},
        "designs": [],
    }

    # 1. strain utilisation against crossing angle and slip (strike-slip, no mitigation)
    for nps in ("24", "30"):
        i_, r_ = sizes[nps]
        pipe = pipe_from_library(nps, class1_wall(r_) * 1000, i_.grade)
        springs = soil_springs(pipe.od_m, basis.cover_m + pipe.od_m / 2, basis.soil,
                               basis.coating)  # fmt: skip
        for slip in SLIPS:
            sw = crossing_angle_sweep(pipe, springs, slip, ANGLES, grade=i_.grade,
                                      pressure=i_.design_pressure_pa_g)  # fmt: skip
            out["sweeps"][f'{nps}" {slip:g} m'] = {
                "wall_mm": round(pipe.wt_m * 1000, 2),
                "utilisation": {
                    int(c.crossing_angle_deg): round(c.utilisation, 2) for c in sw.cases
                },
                "best_angle_deg": sw.best_angle_deg,
            }

    # 2. does the fault change the optimum size? (1 m strike-slip, at 90 deg: orientation
    #    unknown at screening; and the worst practical case, 120 deg, pipe in compression)
    for label, haz in (
        ("1 m strike-slip, crossed at 90 deg", LegHazard(name="Hypothetical fault", kind="fault",
         at_km=AT_KM, displacement_m=1.0, crossing_angle_deg=90)),
        ("1 m strike-slip, crossed at 120 deg (compression)", LegHazard(
         name="Hypothetical fault", kind="fault", at_km=AT_KM, displacement_m=1.0,
         crossing_angle_deg=120)),
        ("1 m reverse, 45 deg dip, crossed at 90 deg", LegHazard(
         name="Hypothetical fault", kind="fault", at_km=AT_KM, displacement_m=1.0,
         crossing_angle_deg=90, dip_deg=45, rake_deg=90)),
        ("2 m strike-slip, crossed at 60 deg (tension)", LegHazard(
         name="Hypothetical fault", kind="fault", at_km=AT_KM, displacement_m=2.0,
         crossing_angle_deg=60)),
    ):  # fmt: skip
        fal = with_fault(al, haz)
        rows = []
        for nps, (i_, r_) in sorted(sizes.items(), key=lambda kv: float(kv[0])):
            a = design_geohazards(fal.geohazard_crossings(), fal.location_class_at, i_, r_, basis)
            assert a is not None
            (c,) = a.checks
            pv = mitigation_pv(a, r_)
            rows.append({
                "nps": nps, "grade": i_.grade,
                "maop_psig": round(i_.design_pressure_pa_g / rs.PSI_TO_PA),
                "wall_mm": c.wall_mm, "strain_utilisation": round(c.utilisation, 2),
                "passes": c.passed, "mitigation": c.mitigation or None,
                "mitigation_wall_mm": c.mitigation_wall_mm,
                "mitigation_capex_usd_m": usd(c.mitigation_cost),
                "best_angle_deg": c.best_crossing_angle_deg,
                "lcc_usd_m": usd(r_.lcc_pv), "lcc_with_mitigation_usd_m": usd(r_.lcc_pv + pv),
                "unmitigated": bool(a.unmitigated),
            })  # fmt: skip
        ok = [r for r in rows if not r["unmitigated"]]
        best = min(ok, key=lambda r: r["lcc_with_mitigation_usd_m"]) if ok else None
        out["designs"].append({"case": label, "rows": rows,
                               "best": best and f'{best["nps"]}" {best["grade"]}'})  # fmt: skip
    out["base_best"] = f'{search.best.nps}" {search.best.grade}'
    out["runtime_s"] = round(time.perf_counter() - t0, 1)
    (HERE / "results").mkdir(exist_ok=True)
    (HERE / "results" / "fault_case.json").write_text(json.dumps(out, indent=1, default=str))
    (HERE / "FAULT_CASE.md").write_text(markdown(out))
    print(json.dumps(out, indent=1, default=str))


def _ur(u: float) -> str:
    return "no convergence" if u == float("inf") else f"{u}"


def markdown(d: dict[str, Any]) -> str:
    sw = d["sweeps"]
    head = "| Pipe, slip | Wall mm | " + " | ".join(f"{a}°" for a in ANGLES) + " | Best |"
    sep = "|---" * (len(ANGLES) + 3) + "|"
    lines = [
        f"| {k} | {v['wall_mm']} | "
        + " | ".join(
            ("**fails**" if u == float("inf") else f"**{u}**") if u > 1 else f"{u}"
            for u in v["utilisation"].values()
        )
        + f" | {v['best_angle_deg']:g}° |"
        for k, v in sw.items()
    ]
    parts = []
    for dsg in d["designs"]:
        rows = "\n".join(
            f'| {r["nps"]}" {r["grade"]} @ {r["maop_psig"]} psig | {r["wall_mm"]} | '
            f"{_ur(r['strain_utilisation'])} | {'pass' if r['passes'] else 'FAIL'} | "
            f"{r['mitigation'] or '-'} | {r['mitigation_capex_usd_m']} | {r['lcc_usd_m']} | "
            f"{r['lcc_with_mitigation_usd_m']} |"
            for r in dsg["rows"]
        )
        parts.append(
            f"### {dsg['case']}\n\n| Design | Wall mm | Strain UR | Result | Mitigation | "
            f"Mitigation CAPEX US$M | LCC US$M | LCC with mitigation US$M |\n|---|---|---|---|"
            f"---|---|---|---|\n{rows}\n\nLowest lifecycle cost with the fault: "
            f"**{dsg['best'] or 'no size is safe without realignment'}**.\n"
        )
    return f"""# Bhola - Khulna: hypothetical fault crossing (what-if)

> **No mapped active fault crosses this corridor.** The known active sources (Dauki,
> Madhupur, Jamuna and Bogra faults, the Chittagong / Arakan belt) lie well to the north
> and east; the southern delta has only buried basin faults. This case tests whether a
> fault found by site investigation would change the design. It is not a hazard finding.

**Set-up.**
* Location: a fault on the Barishal - Khulna leg at km {AT_KM:g} ({d["location"]}).
* Soil: the study's deltaic soft clay (undrained strength 15 kPa, 1.5 m cover, below the
  water table), with ALA springs.
* Analysis: pipe-soil FE model, with ALA strain limits at normal operability and the
  design pressure in the compressive limit (`docs/mechanical-fe.md`).
* Costs: placeholder rates as in the main study.

Base result without the fault: **{d["base_best"]}**.

## 1. Strain utilisation against crossing angle (strike-slip)

The angle is measured from the pipe to the fault strike, for left-lateral slip.
Below 90° the pipe is stretched; above 90° it is compressed. **Bold** = strain limit
exceeded. Class 1 wall.

{head}
{sep}
{chr(10).join(lines)}

## 2. Does the fault change the optimum size?

Each size at its best grade and MAOP from the main study.
* **Mitigation:** the lightest standard wall that passes, over 2 x 100 m about the fault,
  priced with indirects. Its present value is added to lifecycle cost.

{chr(10).join(parts)}
## 3. Reading the results

* **Crossing angle matters far more than pipe size.** For left-lateral slip, crossing at
  60-90° to the strike keeps the recommended 24" X65 within the strain limits on its
  normal 6.5 mm wall for up to 2 m of slip; about 75° is best. Flatter crossings (30-45°)
  over-stretch the pipe at 2 m. Crossings beyond 90° compress it and fail from 0.5-1 m.
  Mirror the angles for right-lateral slip.
* **Compression is a buckling problem.** Where the pipe is shortened (strike-slip beyond
  90°, or a reverse fault), the calculated strains are 5-80 times the limit. The pipe
  would buckle sideways or upwards under the axial compression. The beam model is
  outside its range there, so read these as "fails", not as strain values. The
  heavier-wall results in the 120° case (for example 24.6 mm for 24") show where a
  thick wall stops the buckling. They are indicative only.
* **A reverse fault (1 m slip, 45° dip) fails every size and every standard wall** when
  crossed at 90°. Such a crossing needs realignment and a special design: a wide, loose
  backfill trench, and in some cases a pipe above ground or in a culvert.
* **The fault would not change the optimum diameter.** 24" X65 stays the lowest
  lifecycle-cost design in all three strike-slip cases. Where a heavier wall is enough,
  it costs at most US$0.07M, under 0.1 % of lifecycle cost. A fault is a question of
  crossing design and route, not of pipe size.
* **Before relying on this, a real fault needs the following:**
  * a site-specific fault displacement hazard study (slip, sense, rupture-zone width);
  * soil investigation at the crossing;
  * a shell model of the crossing.

Runtime {d["runtime_s"]} s. Reproduce:
`uv run python studies/bhola_khulna/fault_case.py --workers 4`.
"""


if __name__ == "__main__":
    main()
