# Earthquake liquefaction and lateral spreading

Code: `src/pipeline_analytics/geotech/` (triggering, lateral spread, pipe checks), plus `mechanical/pgd.py` (ground-movement profiles for the FE model). Application: `studies/bhola_khulna/LIQUEFACTION_CASE.md`.

```
SPT profile + earthquake ──► triggering (FS per layer, LPI) ──► T15 / F15 / D50_15
                                                                    │
bank height or slope ──────────────────────────────► lateral spread D_H(distance) (Youd 2002)
                                                                    │
pipe, ALA springs ──► FE model: HDD exit setback check ◄────────────┘ ; flotation check
```

## 1. Triggering: Idriss & Boulanger (2008), SPT (`geotech/liquefaction.py`)

| Step | Equation |
|---|---|
| Cyclic stress ratio | CSR = 0.65 (a_max/g)(σ_v/σ′_v) r_d, where r_d = exp(α + βM), α = −1.012 − 1.126 sin(z/11.73 + 5.133), β = 0.106 + 0.118 sin(z/11.28 + 5.142) |
| Overburden normalisation | (N1)60 = C_N N60, C_N = (P_a/σ′_v)^m ≤ 1.7, m = 0.784 − 0.0768 √(N1)60cs (iterated) |
| Fines | (N1)60cs = (N1)60 + exp(1.63 + 9.7/(FC+0.01) − (15.7/(FC+0.01))²) |
| Resistance | CRR(M 7.5, 1 atm) = exp(N/14.1 + (N/126)² − (N/23.6)³ + (N/25.4)⁴ − 2.8), N = (N1)60cs |
| Magnitude and overburden | MSF = 6.9 exp(−M/4) − 0.058 ≤ 1.8; K_σ = 1 − C_σ ln(σ′_v/P_a) ≤ 1.1, C_σ = 1/(18.9 − 2.55 √N) ≤ 0.3 |
| Factor of safety | FS = CRR(M 7.5, 1 atm) · MSF · K_σ / CSR |

**Inputs and exclusions:**
* N60 must already be corrected for hammer energy, rod length, sampler and borehole.
* Layers above the water table, and clay-like layers (PI ≥ 7), are not susceptible.

**Outputs:**
* the factor of safety per layer;
* the liquefaction potential index, LPI (Iwasaki: < 5 low, 5–15 high, > 15 very high);
* T15 / F15 / D50₁₅ for the lateral-spread model. These come from layers that liquefy and have (N1)60 < 15; no such layer means no spread.

## 2. Lateral spread: Youd, Hansen & Bartlett (2002) (`geotech/lateral_spread.py`)

log D_H = b₀ + 1.532 M − 1.406 log R* − 0.012 R + b_g log G + 0.540 log T15 + 3.413 log(100 − F15) − 0.795 log(D50₁₅ + 0.1)

* **Free face** (river bank): b₀ = −16.713 and G = W = 100 H/L (%), b_g = 0.592.
* **Ground slope:** b₀ = −16.213 and G = S (%), b_g = 0.338.
* **Distance term:** R* = R + 10^(0.89M − 5.64).

Behind a bank, D_H is computed against distance from the toe. W is capped at 20 % near the face (the edge of the data), and the profile runs out to W = 1 % or D_H < 0.05 m, then tapers to zero. The regression has a scatter of roughly ×2. Inputs outside the data range are flagged: M 6–8, W 1–20 %, S 0.1–6 %, T15 1–15 m, F15 ≤ 50 %, D50₁₅ 0.1–1 mm.

## 3. Pipe checks (`geotech/pipe.py`, `mechanical/pgd.py`)

* **Bank spread at an HDD exit.** `lateral_spread_movement(spread, skew, hdd_setback_m, transition_m)` builds a tabulated ground movement for the FE model:
  * The ground moves towards the river, following the D_H profile inland from where the pipe leaves the HDD.
  * Under the river the pipe is deep and the ground is stable.
  * An optional transition represents the HDD climbing through the liquefiable layer.

  `bank_spread_check` scans exit setbacks and returns the strain utilisation of each and the smallest setback that passes. Two findings from testing:
  * Under axial (friction-driven) loading, a longer transition increases the compression slightly, because more moving ground drags on the pipe.
  * Skew to the bank normal adds a transverse component and is much more severe.
* **Pipe along a bank:** `parallel_spread_movement` (transverse block with the local D_H).
* **Liquefied springs:** `analyse_pgd(..., moving_ground_spring_factor=0.1)` scales the springs where the ground moves. The default of 1.0 keeps full springs, forcing the pipe to follow the ground: an upper bound on strain.
* **Flotation:** `flotation_check` treats liquefied soil as a heavy fluid (default 1850 kg/m³, ALA 2001) and reports the net uplift on an empty pipe and the concrete coating that would hold it down.

**Where it appears:**
* the bot tool `liquefaction_check`;
* the Streamlit Mechanical tab ("Earthquake liquefaction and lateral spreading"), with an editable SPT table.

## 4. Verification (`tests/geotech/test_liquefaction.py`)

* **Triggering:** an independent hand calculation of one layer (stresses, C_N iteration, fines, CRR, K_σ cap, FS). Component checks:
  * MSF(7.5) = 1.00 and its cap of 1.8;
  * CRR at (N1)60cs = 30 is 0.485;
  * the fines adjustment is 0 for clean sand and about 5.5 on its plateau;
  * r_d(0) = 1.
* **Lateral spread:**
  * the equation reproduced term by term;
  * the free-face versus ground-slope offset;
  * the W^0.592 decay;
  * D_H falling as R increases.
* **Pipe:**
  * utilisation falls as the setback grows;
  * the required setback is found;
  * liquefied springs reduce the load;
  * flotation is resolved by the computed coating.

**Limitations:**
* The regressions are empirical. Use site SPT/CPT data, and a site-specific seismic hazard study for magnitude, distance and PGA.
* Settlement from reconsolidation is not yet computed.
* Cyclic softening of clays is outside the method.
