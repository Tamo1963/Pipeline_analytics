# Bhola - Khulna: hypothetical fault crossing (what-if)

> **No mapped active fault crosses this corridor.** The known active sources (Dauki,
> Madhupur, Jamuna and Bogra faults, the Chittagong / Arakan belt) lie well to the north
> and east; the southern delta has only buried basin faults. This case tests whether a
> fault found by site investigation would change the design. It is not a hazard finding.

**Set-up.**
* Location: a fault on the Barishal - Khulna leg at km 60 (leg 2 km 60 (Class 1)).
* Soil: the study's deltaic soft clay (undrained strength 15 kPa, 1.5 m cover, below the
  water table), with ALA springs.
* Analysis: pipe-soil FE model, with ALA strain limits at normal operability and the
  design pressure in the compressive limit (`docs/mechanical-fe.md`).
* Costs: placeholder rates as in the main study.

Base result without the fault: **24" X65**.

## 1. Strain utilisation against crossing angle (strike-slip)

The angle is measured from the pipe to the fault strike, for left-lateral slip.
Below 90° the pipe is stretched; above 90° it is compressed. **Bold** = strain limit
exceeded. Class 1 wall.

| Pipe, slip | Wall mm | 30° | 45° | 60° | 75° | 90° | 105° | 120° | 135° | 150° | Best |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 24" 0.5 m | 6.5 | 0.25 | 0.23 | 0.2 | 0.15 | 0.29 | 0.87 | **1.68** | **2.83** | **4.03** | 75° |
| 24" 1 m | 6.5 | 0.71 | 0.54 | 0.38 | 0.3 | 0.44 | **5.11** | **25.86** | **40.87** | **49.03** | 75° |
| 24" 2 m | 6.5 | **1.77** | **1.4** | 0.92 | 0.42 | 0.46 | **22.86** | **47.63** | **66.8** | **77.36** | 75° |
| 30" 0.5 m | 8.0 | 0.15 | 0.15 | 0.14 | 0.12 | 0.25 | 0.52 | 0.74 | 0.89 | 0.85 | 75° |
| 30" 1 m | 8.0 | 0.44 | 0.34 | 0.28 | 0.22 | 0.35 | **2.09** | **11.68** | **38.93** | **fails** | 75° |
| 30" 2 m | 8.0 | **1.45** | **1.08** | 0.61 | 0.34 | 0.44 | **18.71** | **49.15** | **66.16** | **fails** | 75° |

## 2. Does the fault change the optimum size?

Each size at its best grade and MAOP from the main study.
* **Mitigation:** the lightest standard wall that passes, over 2 x 100 m about the fault,
  priced with indirects. Its present value is added to lifecycle cost.

### 1 m strike-slip, crossed at 90 deg

| Design | Wall mm | Strain UR | Result | Mitigation | Mitigation CAPEX US$M | LCC US$M | LCC with mitigation US$M |
|---|---|---|---|---|---|---|---|
| 18" X70 @ 1440 psig | 6.5 | 0.28 | pass | - | 0.0 | 110.89 | 110.89 |
| 20" X70 @ 1000 psig | 5.3 | 0.43 | pass | - | 0.0 | 87.99 | 87.99 |
| 24" X65 @ 1000 psig | 6.5 | 0.44 | pass | - | 0.0 | 83.19 | 83.19 |
| 26" X70 @ 1000 psig | 6.9 | 0.38 | pass | - | 0.0 | 86.72 | 86.72 |
| 28" X65 @ 1000 psig | 7.6 | 0.4 | pass | - | 0.0 | 91.57 | 91.57 |
| 30" X70 @ 1000 psig | 8.0 | 0.35 | pass | - | 0.0 | 97.19 | 97.19 |
| 36" X65 @ 1000 psig | 9.8 | 0.34 | pass | - | 0.0 | 116.65 | 116.65 |

Lowest lifecycle cost with the fault: **24" X65**.

### 1 m strike-slip, crossed at 120 deg (compression)

| Design | Wall mm | Strain UR | Result | Mitigation | Mitigation CAPEX US$M | LCC US$M | LCC with mitigation US$M |
|---|---|---|---|---|---|---|---|
| 18" X70 @ 1440 psig | 6.5 | 26.33 | FAIL | no standard wall passes: realign the crossing (best angle 75 deg to the strike) or design a special crossing (loose backfill trench, shell analysis) | 0.0 | 110.89 | 110.89 |
| 20" X70 @ 1000 psig | 5.3 | 39.2 | FAIL | no standard wall passes: realign the crossing (best angle 75 deg to the strike) or design a special crossing (loose backfill trench, shell analysis) | 0.0 | 87.99 | 87.99 |
| 24" X65 @ 1000 psig | 6.5 | 25.86 | FAIL | heavier wall 24.61 mm over 200 m (strain UR 0.40) | 0.07 | 83.19 | 83.25 |
| 26" X70 @ 1000 psig | 6.9 | 25.27 | FAIL | no standard wall passes: realign the crossing (best angle 75 deg to the strike) or design a special crossing (loose backfill trench, shell analysis) | 0.0 | 86.72 | 86.72 |
| 28" X65 @ 1000 psig | 7.6 | 19.05 | FAIL | no standard wall passes: realign the crossing (best angle 75 deg to the strike) or design a special crossing (loose backfill trench, shell analysis) | 0.0 | 91.57 | 91.57 |
| 30" X70 @ 1000 psig | 8.0 | 11.68 | FAIL | heavier wall 15.88 mm over 200 m (strain UR 0.83) | 0.04 | 97.19 | 97.23 |
| 36" X65 @ 1000 psig | 9.8 | 4.84 | FAIL | heavier wall 19.05 mm over 200 m (strain UR 0.65) | 0.05 | 116.65 | 116.7 |

Lowest lifecycle cost with the fault: **24" X65**.

### 1 m reverse, 45 deg dip, crossed at 90 deg

| Design | Wall mm | Strain UR | Result | Mitigation | Mitigation CAPEX US$M | LCC US$M | LCC with mitigation US$M |
|---|---|---|---|---|---|---|---|
| 18" X70 @ 1440 psig | 6.5 | 45.73 | FAIL | no standard wall passes: realign the crossing (best angle 150 deg to the strike) or design a special crossing (loose backfill trench, shell analysis) | 0.0 | 110.89 | 110.89 |
| 20" X70 @ 1000 psig | 5.3 | 70.89 | FAIL | no standard wall passes: realign the crossing (best angle 150 deg to the strike) or design a special crossing (loose backfill trench, shell analysis) | 0.0 | 87.99 | 87.99 |
| 24" X65 @ 1000 psig | 6.5 | 48.89 | FAIL | no standard wall passes: realign the crossing (best angle 150 deg to the strike) or design a special crossing (loose backfill trench, shell analysis) | 0.0 | 83.19 | 83.19 |
| 26" X70 @ 1000 psig | 6.9 | 53.8 | FAIL | no standard wall passes: realign the crossing (best angle 30 deg to the strike) or design a special crossing (loose backfill trench, shell analysis) | 0.0 | 86.72 | 86.72 |
| 28" X65 @ 1000 psig | 7.6 | 38.63 | FAIL | no standard wall passes: realign the crossing (best angle 30 deg to the strike) or design a special crossing (loose backfill trench, shell analysis) | 0.0 | 91.57 | 91.57 |
| 30" X70 @ 1000 psig | 8.0 | no convergence | FAIL | no standard wall passes: realign the crossing (best angle 30 deg to the strike) or design a special crossing (loose backfill trench, shell analysis) | 0.0 | 97.19 | 97.19 |
| 36" X65 @ 1000 psig | 9.8 | 28.82 | FAIL | no standard wall passes: realign the crossing (best angle 30 deg to the strike) or design a special crossing (loose backfill trench, shell analysis) | 0.0 | 116.65 | 116.65 |

Lowest lifecycle cost with the fault: **no size is safe without realignment**.

### 2 m strike-slip, crossed at 60 deg (tension)

| Design | Wall mm | Strain UR | Result | Mitigation | Mitigation CAPEX US$M | LCC US$M | LCC with mitigation US$M |
|---|---|---|---|---|---|---|---|
| 18" X70 @ 1440 psig | 6.5 | 0.89 | pass | - | 0.0 | 110.89 | 110.89 |
| 20" X70 @ 1000 psig | 5.3 | 1.27 | FAIL | heavier wall 6.35 mm over 200 m (strain UR 0.93) | 0.0 | 87.99 | 88.0 |
| 24" X65 @ 1000 psig | 6.5 | 0.92 | pass | - | 0.0 | 83.19 | 83.19 |
| 26" X70 @ 1000 psig | 6.9 | 0.81 | pass | - | 0.0 | 86.72 | 86.72 |
| 28" X65 @ 1000 psig | 7.6 | 0.74 | pass | - | 0.0 | 91.57 | 91.57 |
| 30" X70 @ 1000 psig | 8.0 | 0.61 | pass | - | 0.0 | 97.19 | 97.19 |
| 36" X65 @ 1000 psig | 9.8 | 0.51 | pass | - | 0.0 | 116.65 | 116.65 |

Lowest lifecycle cost with the fault: **24" X65**.

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

Runtime 500.7 s. Reproduce:
`uv run python studies/bhola_khulna/fault_case.py --workers 4`.
