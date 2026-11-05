"""Render the shareable HTML report page from results/summary.json.

    uv run python studies/bhola_khulna/make_html.py results/summary.json report.html

Styled with the IBM Plex / navy-teal palette of the owner's engineering design system.
"""

import html
import json
import sys
from typing import Any

with open(sys.argv[1]) as fh:
    d: Any = json.load(fh)
A, B = d["alignments"]["A_note"], d["alignments"]["B_gtcl"]
res, cal, sens = d["reserves"], d["calibration"], d["sensitivity"]
best = A["best"]
a30 = A["by_size"]["30"]
prem = (a30["lcc_usd_m"] / best["lcc_usd_m"] - 1) * 100
e = html.escape


def f1(x: float) -> str:
    return f"{x:,.1f}"


def size_rows(al: dict[str, Any]) -> str:
    out = []
    for nps, r in al["by_size"].items():
        if "infeasible" in r:
            out.append(
                f'<tr class="na"><td class="num">{nps}"</td><td colspan="9">Not feasible: gas velocity above the erosional limit</td></tr>'
            )
            continue
        w = r["walls_mm"]
        walls = " / ".join(f"{w.get(k, '–')}" for k in ("1-2", "2", "3"))
        is_best = nps == al["best"]["nps"]
        cls = ' class="best"' if is_best else (' class="note"' if nps == "30" else "")
        tag = (
            ' <span class="tag t-best">Lowest LCC</span>'
            if is_best
            else (' <span class="tag t-note">Note</span>' if nps == "30" else "")
        )
        out.append(
            f'<tr{cls}><td class="num">{nps}"{tag}</td><td>{r["grade"]}</td><td class="num">{r["maop_psig"]}</td>'
            f'<td class="num">{walls}</td><td class="num">{r["capex_usd_m"]:.1f}</td><td class="num">{r["pv_capex_usd_m"]:.1f}</td>'
            f'<td class="num">{r["pv_opex_usd_m"]:.1f}</td><td class="num strong">{r["lcc_usd_m"]:.1f}</td>'
            f'<td class="num">{r["max_stations"]}</td><td class="num">{r["first_compression_year"]}</td></tr>'
        )
    return "\n".join(out)


# ---- chart: stacked PV CAPEX + PV OPEX by size, route A
pts = [(n, r) for n, r in A["by_size"].items() if "infeasible" not in r]
W, H, L, R, T, Bm = 640, 300, 56, 16, 24, 44
vmax = 120.0
cw = (W - L - R) / len(pts)
bw = cw * 0.56


def y(v: float) -> float:
    return T + (H - T - Bm) * (1 - v / vmax)


svg = [
    f'<svg viewBox="0 0 {W} {H}" role="img" aria-labelledby="ch-t ch-d" class="chart"><title id="ch-t">Lifecycle cost by pipe size, route A</title><desc id="ch-d">Stacked bars of present-value CAPEX and OPEX in US$ million for each feasible pipe size.</desc>'
]
for v in range(0, 121, 20):
    svg.append(
        f'<line x1="{L}" x2="{W - R}" y1="{y(v):.1f}" y2="{y(v):.1f}" class="grid"/><text x="{L - 8}" y="{y(v) + 4:.1f}" class="ax" text-anchor="end">{v}</text>'
    )
for i, (n, r) in enumerate(pts):
    x = L + i * cw + (cw - bw) / 2
    c, o = r["pv_capex_usd_m"], r["pv_opex_usd_m"]
    svg.append(
        f'<rect x="{x:.1f}" y="{y(c):.1f}" width="{bw:.1f}" height="{y(0) - y(c):.1f}" class="b-capex"><title>{n}": PV CAPEX {c:.1f}</title></rect>'
    )
    svg.append(
        f'<rect x="{x:.1f}" y="{y(c + o):.1f}" width="{bw:.1f}" height="{y(c) - y(c + o):.1f}" class="b-opex"><title>{n}": PV OPEX {o:.1f}</title></rect>'
    )
    lab_cls = "lab strong" if n == best["nps"] else "lab"
    svg.append(
        f'<text x="{x + bw / 2:.1f}" y="{y(c + o) - 6:.1f}" class="{lab_cls}" text-anchor="middle">{r["lcc_usd_m"]:.1f}</text>'
    )
    svg.append(
        f'<text x="{x + bw / 2:.1f}" y="{H - Bm + 18:.1f}" class="ax" text-anchor="middle">{n}"</text>'
    )
    if n == best["nps"]:
        svg.append(
            f'<rect x="{x - 3:.1f}" y="{H - Bm + 26:.1f}" width="{bw + 6:.1f}" height="6" class="mark"/>'
        )
svg.append(f'<text x="{L}" y="14" class="ax">US$ million (PV)</text>')
svg.append("</svg>")
chart = "\n".join(svg)


def bar(p: float) -> str:
    w = max(0.0, min(p, 45.0)) / 45 * 100
    return f'<span class="pbar"><span style="width:{w:.0f}%"></span></span>'


srows = "\n".join(
    f'<tr><td>{e(s["case"])}</td><td class="num">{s["best_nps"]}" {s["best_grade"]}</td><td class="num">{s["best_lcc_usd_m"]:.1f}</td>'
    f'<td class="num">{s["lcc_30in_usd_m"]:.1f}</td><td class="num prem">{s["30in_premium_pct"]:.1f}% {bar(s["30in_premium_pct"])}</td>'
    f'<td class="num">{s["best_tariff_usd_per_mmbtu"]:.3f}</td></tr>'
    for s in sens
)

wins = sorted({s["best_nps"] for s in sens}, key=float)
page = f"""<title>Bhola–Khulna Pipeline Sizing</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600;700&display=swap">
<style>
/* Layout: navy title band, then a single 68ch reading column with wide tables and the chart breaking out to 960px. */
:root {{
  --surface-100:#f7f8fa; --surface-200:#eaeef3; --ink:#0f1f33; --muted:#4a5b70; --line:#c9d2dc;
  --brand:#12324f; --brand-ink:#ffffff; --brand-deep:#0a2136; --accent:#0b7a75; --signal:#e8a317;
  --danger:#b3261e; --success:#1e7a4a; --band-ink:#ffffff; --band-muted:#b9c6d4;
  --sans:"IBM Plex Sans","Segoe UI",system-ui,sans-serif; --mono:"IBM Plex Mono",ui-monospace,Consolas,monospace;
}}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{
  --surface-100:#0c1826; --surface-200:#142438; --ink:#e9eef4; --muted:#9db0c4; --line:#2a3d53;
  --brand:#5aa2d8; --brand-ink:#06121f; --brand-deep:#1b3a5a; --accent:#3fc1b8; --signal:#f2b53a;
  --danger:#ff8a80; --success:#62d08f; --band-ink:#e9eef4; --band-muted:#a9bccf; color-scheme:dark; }} }}
:root[data-theme="dark"] {{
  --surface-100:#0c1826; --surface-200:#142438; --ink:#e9eef4; --muted:#9db0c4; --line:#2a3d53;
  --brand:#5aa2d8; --brand-ink:#06121f; --brand-deep:#1b3a5a; --accent:#3fc1b8; --signal:#f2b53a;
  --danger:#ff8a80; --success:#62d08f; --band-ink:#e9eef4; --band-muted:#a9bccf; color-scheme:dark; }}
body {{ background:var(--surface-100); color:var(--ink); font:400 15px/23px var(--sans); }}
.band {{ background:var(--brand-deep); color:var(--band-ink);
  background-image:radial-gradient(color-mix(in srgb, var(--band-ink) 14%, transparent) 1px, transparent 1px); background-size:16px 16px; }}
.wrap {{ max-width:960px; margin:0 auto; padding-left:max(16px, env(safe-area-inset-left, 0px)); padding-right:max(16px, env(safe-area-inset-right, 0px)); }}
.band .wrap {{ padding-block:48px 40px; }}
.kicker {{ font:500 12px/16px var(--mono); letter-spacing:.08em; text-transform:uppercase; color:var(--band-muted); }}
h1 {{ font-weight:700; font-size:clamp(30px, 5vw, 44px); line-height:1.12; letter-spacing:-.5px; margin:10px 0 12px; text-wrap:balance; max-width:22ch; }}
.lede {{ color:var(--band-muted); max-width:62ch; margin:0; }}
main .wrap {{ padding-block:32px 48px; display:flex; flex-direction:column; gap:32px; }}
.col {{ max-width:68ch; }}
h2 {{ font-size:20px; line-height:28px; font-weight:600; margin:0 0 12px; text-wrap:balance; display:flex; gap:12px; align-items:baseline; }}
h2 .no {{ font:500 13px/20px var(--mono); color:var(--muted); }}
p {{ margin:0 0 12px; }}
.status {{ display:flex; gap:12px; align-items:flex-start; border:1px solid var(--line); border-radius:8px; padding:16px; background:var(--surface-200); }}
.status::before {{ content:""; flex:0 0 10px; height:10px; margin-top:7px; border-radius:2px; background:var(--brand); }}
.status p {{ margin:0; font-size:13px; line-height:20px; }}
.answer {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(200px, 1fr)); gap:16px; }}
.fig {{ border-top:2px solid var(--brand); padding-top:12px; min-width:0; }}
.fig .v {{ font:500 26px/32px var(--mono); color:var(--brand); }}
.fig .k {{ font-size:13px; line-height:20px; color:var(--muted); }}
ul.points {{ margin:0; padding-left:20px; display:flex; flex-direction:column; gap:8px; }}
.mono, .num {{ font-family:var(--mono); font-size:13px; font-variant-numeric:tabular-nums; }}
.tbl {{ overflow-x:auto; border:1px solid var(--line); border-radius:4px; }}
table {{ border-collapse:collapse; width:100%; font-size:13px; line-height:20px; }}
th, td {{ padding:8px 12px; text-align:left; border-bottom:1px solid var(--line); vertical-align:top; }}
th {{ background:var(--surface-200); font-weight:600; white-space:nowrap; vertical-align:bottom; }}
td.num, th.num {{ text-align:right; white-space:nowrap; }}
tr:last-child td {{ border-bottom:0; }}
tr.best td {{ background:color-mix(in srgb, var(--accent) 12%, var(--surface-100)); }}
tr.na td {{ color:var(--muted); }}
.strong {{ font-weight:600; }}
.tag {{ font:500 11px/16px var(--sans); letter-spacing:.04em; text-transform:uppercase; border-radius:2px; padding:1px 6px; margin-left:6px; white-space:nowrap; }}
.t-best {{ background:var(--accent); color:var(--surface-100); }}
.t-note {{ border:1px solid var(--muted); color:var(--muted); }}
.caption {{ font-size:12px; line-height:16px; color:var(--muted); margin-top:8px; }}
.chart {{ width:100%; height:auto; max-width:100%; display:block; }}
.chart .grid {{ stroke:var(--line); stroke-width:1; }}
.chart .ax {{ fill:var(--muted); font:400 11px var(--mono); }}
.chart .lab {{ fill:var(--ink); font:400 11px var(--mono); }}
.chart .lab.strong {{ font-weight:600; }}
.chart .b-capex {{ fill:var(--brand); }}
.chart .b-opex {{ fill:var(--accent); }}
.chart .mark {{ fill:var(--signal); }}
.legend {{ display:flex; gap:16px; flex-wrap:wrap; font-size:12px; color:var(--muted); }}
.legend i {{ display:inline-block; width:10px; height:10px; border-radius:2px; margin-right:6px; vertical-align:-1px; }}
.pbar {{ display:inline-block; width:64px; height:6px; background:var(--surface-200); border-radius:2px; margin-left:8px; vertical-align:1px; }}
.pbar span {{ display:block; height:100%; background:var(--accent); border-radius:2px; }}
.two {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(280px, 1fr)); gap:24px; }}
.two > div {{ min-width:0; }}
dl.kv {{ display:grid; grid-template-columns:auto 1fr; gap:6px 16px; margin:0; font-size:13px; line-height:20px; }}
dl.kv dt {{ color:var(--muted); }}
dl.kv dd {{ margin:0; font-family:var(--mono); text-align:right; font-variant-numeric:tabular-nums; }}
ol.next {{ margin:0; padding-left:20px; display:flex; flex-direction:column; gap:8px; }}
a {{ color:var(--brand); }}
footer {{ border-top:1px solid var(--line); }}
footer .wrap {{ padding-block:24px; font-size:12px; line-height:18px; color:var(--muted); }}
@media (max-width:520px) {{ th, td {{ padding:6px 8px; }} }}
</style>

<header class="band"><div class="wrap">
  <div class="kicker">Screening optimisation · 180 MMSCFD gas evacuation</div>
  <h1>Bhola–Barishal–Khulna Gas Pipeline Sizing</h1>
  <p class="lede">Which pipe diameter gives the lowest cost over the life of the project, tested on the route in the client note and on GTCL's published route, with {len(sens)} sensitivity cases.</p>
</div></header>

<main><div class="wrap">

<div class="status col"><p><strong>Screening study.</strong> Costs use placeholder Indian unit rates as a regional proxy, converted at an assumed INR 88 per US$. River crossing lengths, wetland share, crossings and towns are engineering assumptions, not survey data. Use the results to compare options, not as a budget.</p></div>

<section>
  <h2><span class="no">1</span>Answer</h2>
  <div class="answer">
    <div class="fig"><div class="v">{best["nps"]}" {best["grade"]}</div><div class="k">Lowest lifecycle cost, at {best["maop_psig"]} psig MAOP</div></div>
    <div class="fig"><div class="v">+{prem:.0f}%</div><div class="k">Extra lifecycle cost of the note's 30" design</div></div>
    <div class="fig"><div class="v">Year {best["first_compression_year"]}</div><div class="k">First compression needed, as field pressure falls</div></div>
  </div>
</section>

<section class="col">
  <ul class="points">
    <li>For 180 MMSCFD on the note's 173 km route, the optimum is <strong>{best["nps"]}" {best["grade"]} at {best["maop_psig"]} psig</strong>, with a lifecycle cost of US$ {best["lcc_usd_m"]:.1f}M (present value). The 30" pipe in the note costs US$ {a30["lcc_usd_m"]:.1f}M.</li>
    <li>The result holds across all {len(sens)} sensitivity cases: the best size is {" or ".join(w + '"' for w in wins)}, never 30". The gap narrows most if throughput rises to 260 MMSCFD.</li>
    <li>Compressors are needed only as the Bhola fields deplete. On {best["nps"]}" they are first needed in year {best["first_compression_year"]} (peak {best["peak_compression_mw"]} MW), so they can be phased in rather than built on day one.</li>
    <li>Reserves limit the plateau. Of {res["remaining_recoverable_tcf"]} Tcf remaining recoverable gas, 180 MMSCFD lasts <strong>{res["years_at_pipeline_rate_only"]} years</strong>, but only <strong>{res["years_if_local_use_continues"]} years</strong> if the island's existing ~80 MMSCFD power and CNG use continues.</li>
  </ul>
</section>

<section>
  <h2><span class="no">2</span>Lifecycle cost by pipe size</h2>
  <p class="col">Route A (the note's route, {A["length_km"]:.0f} km). Each bar is the present value of building plus running costs over a {d["sensitivity"][0]["case"].split("(")[1].split(",")[0]} operating period, at the best grade and pressure for that size.</p>
  {chart}
  <div class="legend"><span><i style="background:var(--brand)"></i>PV CAPEX</span><span><i style="background:var(--accent)"></i>PV OPEX (fuel, O&amp;M, inspection, insurance)</span><span><i style="background:var(--signal)"></i>Lowest lifecycle cost</span></div>
  <p class="caption">16" is not shown: gas velocity exceeds the erosional limit. Smaller pipes are cheaper to build but need more compression for longer; larger pipes cost more steel than they save in fuel.</p>
</section>

<section>
  <h2><span class="no">3</span>Route A: the note's route via Laharhat</h2>
  <div class="tbl"><table>
    <thead><tr><th>NPS</th><th>Grade</th><th class="num">MAOP<br>psig</th><th class="num">Wall mm<br>C1 / C2 / C3</th><th class="num">CAPEX<br>US$M</th><th class="num">PV<br>CAPEX</th><th class="num">PV<br>OPEX</th><th class="num">LCC<br>US$M</th><th class="num">Comp.<br>stations</th><th class="num">Comp.<br>from yr</th></tr></thead>
    <tbody>{size_rows(A)}</tbody>
  </table></div>
  <p class="caption">C1 / C2 / C3: location classes (rural, town approaches, towns). {A["length_km"]:.0f} km · {A["hdd_m"]:,.0f} m river HDD (assumed) · {A["wet_length_km"]} km needing concrete weight coating · walls from ASME B31.8 hoop stress or the D/t ≤ 96 handling limit; ALA buried-pipe checks pass on every size.</p>
</section>

<section>
  <h2><span class="no">4</span>Route B: GTCL's route via Mehendiganj and Hizla</h2>
  <div class="tbl"><table>
    <thead><tr><th>NPS</th><th>Grade</th><th class="num">MAOP<br>psig</th><th class="num">Wall mm<br>C1 / C2 / C3</th><th class="num">CAPEX<br>US$M</th><th class="num">PV<br>CAPEX</th><th class="num">PV<br>OPEX</th><th class="num">LCC<br>US$M</th><th class="num">Comp.<br>stations</th><th class="num">Comp.<br>from yr</th></tr></thead>
    <tbody>{size_rows(B)}</tbody>
  </table></div>
  <p class="caption">{B["length_km"]:.0f} km, {B["length_km"] - A["length_km"]:.0f} km longer than route A, with {B["hdd_m"] - A["hdd_m"]:,.0f} m more river HDD (assumed). It costs more on every size here, but this route came from a feasibility study of river erosion, so route choice should follow that evidence, not cost alone.</p>
</section>

<section>
  <h2><span class="no">5</span>Sensitivity</h2>
  <p class="col">Route A, with the design re-optimised in each case over 20–30" pipe, grades X65 and X70, and MAOP 1000 or 1440 psig.</p>
  <div class="tbl"><table>
    <thead><tr><th>Case</th><th class="num">Best size</th><th class="num">LCC US$M</th><th class="num">30" LCC US$M</th><th class="num">30" premium</th><th class="num">Tariff US$/MMBtu</th></tr></thead>
    <tbody>{srows}</tbody>
  </table></div>
  <p class="caption">Tariff: constant charge for a 12% post-tax return. The calibrated case scales every capital rate by {cal["capital_rate_factor"]} so that GTCL's own design matches its published Tk 4,500 crore budget.</p>
</section>

<section class="two">
  <div>
    <h2><span class="no">6</span>Discrepancies in the inputs</h2>
    <div class="tbl"><table>
      <thead><tr><th>Item</th><th>Note</th><th>GTCL (press)</th></tr></thead>
      <tbody>
        <tr><td>Length</td><td class="num">63 + 110 = 173 km</td><td class="num">96 + 109 = 205 km</td></tr>
        <tr><td>Diameter</td><td>30" throughout</td><td>24" to Barishal, 30" on</td></tr>
        <tr><td>Recoverable gas</td><td>"1,432 trillion cu ft"</td><td class="num">1.432 Tcf</td></tr>
        <tr><td>Capital cost</td><td>–</td><td class="num">Tk 4,500 crore</td></tr>
      </tbody>
    </table></div>
    <p class="caption">The note's reserve figure is a units error: 1,432 billion cubic feet is 1.432 Tcf.</p>
  </div>
  <div>
    <h2><span class="no">7</span>Absolute cost</h2>
    <dl class="kv">
      <dt>GTCL published budget</dt><dd>US$ {cal["gtcl_budget_usd_m"]:.0f}M</dd>
      <dt>This model, GTCL design</dt><dd>US$ {cal["model_gtcl_design_usd_m"]:.0f}M</dd>
      <dt>Ratio</dt><dd>{cal["capital_rate_factor"]}×</dd>
    </dl>
    <p class="caption">The gap reflects Bangladesh costs, kilometre-wide river crossings, bank protection, field gathering lines, land, duties and financing. Scaling all capital rates by this factor does not change the optimum diameter.</p>
  </div>
</section>

<section class="col">
  <h2><span class="no">8</span>Data that would firm this up</h2>
  <ol class="next">
    <li><strong>Design flow and its duration</strong>, including any national-grid transit or proven upside reserves. Higher flow favours a larger pipe.</li>
    <li><strong>Field pressure forecast</strong> from Bapex. If supply holds at 62 barg, the optimum drops to 20".</li>
    <li><strong>River crossing survey</strong>: widths, scour and erosion from the CEGIS / ILF study, and the crossing method. This is the largest cost and construction risk.</li>
    <li><strong>Bangladesh unit rates</strong> or the GTCL estimate breakdown.</li>
    <li><strong>Offtake split and delivery pressures</strong> at Barishal and Khulna.</li>
  </ol>
</section>

<section class="col">
  <h2><span class="no">9</span>Main assumptions</h2>
  <dl class="kv">
    <dt>Throughput</dt><dd>180 MMSCFD, {d["sensitivity"][0]["case"].split("(")[1].split(",")[0]}</dd>
    <dt>Barishal offtake</dt><dd>1/3 of flow, ≥ 25 barg</dd>
    <dt>Khulna delivery</dt><dd>≥ 40 barg</dd>
    <dt>Field supply pressure</dt><dd>62 → 35 barg</dd>
    <dt>MAOP options</dt><dd>1000 / 1440 psig</dd>
    <dt>Compressor driver</dt><dd>gas turbine</dd>
    <dt>River HDD, route A</dt><dd>6 km + 3 km</dd>
    <dt>Soil</dt><dd>soft clay, water at surface</dd>
    <dt>Discount rate (real)</dt><dd>10%</dd>
    <dt>Construction</dt><dd>2 years, BOO</dd>
  </dl>
</section>

</div></main>
<footer><div class="wrap">Sources: client note on the Bhola gas pipeline; The Business Standard, "Tk4,500cr pipeline planned to feed Bhola gas to energy-poor South" (GTCL proposal and Bapex reserve figures). Calculations: ASME B31.8 pressure design, ALA buried-pipe checks, steady-state gas hydraulics with phased compression, and lifecycle cost over the concession. {d["evaluations"]} design options evaluated.</div></footer>
"""
with open(sys.argv[2], "w") as fh:
    fh.write(page)
print("ok", len(page))
