# Bhola - Khulna: liquefaction and lateral spreading at river banks (what-if)

> **No borehole data.** The soil profile is a generic young deltaic river bank and the
> earthquakes are assumptions anchored to BNBC 2020 seismic Zone 1 (Z = 0.12 g) for
> Barishal and Khulna, amplified x1.5 for soft soil (assumed). Use this
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
| 0-2 | silty clay crust | 4 | 85 | - |
| 2-5 | loose silty fine sand | 6 | 30 | 0.1 |
| 5-9 | loose to medium fine sand | 9 | 15 | 0.15 |
| 9-15 | medium dense fine sand | 18 | 10 | 0.2 |
| 15-25 | dense sand | 30 | 8 | 0.25 |

## Lateral spread and HDD setback

| Earthquake | LPI | Bank m | Spread at toe m | Extent m | Design | Setback m | Extra HDD US$M |
|---|---|---|---|---|---|---|---|
| BNBC Zone 1, M 7.5 at 30 km | 10.0 | 3 | 2.27 | 293 | 24" X65 (6.5 mm) | 0 | 0.0 |
| BNBC Zone 1, M 7.5 at 30 km | 10.0 | 3 | 2.27 | 293 | 30" X70 (8.0 mm) | 0 | 0.0 |
| BNBC Zone 1, M 7.5 at 30 km | 10.0 | 5 | 2.27 | 488 | 24" X65 (6.5 mm) | 50 | 0.31 |
| BNBC Zone 1, M 7.5 at 30 km | 10.0 | 5 | 2.27 | 488 | 30" X70 (8.0 mm) | 0 | 0.0 |
| BNBC Zone 1, M 7.5 at 30 km | 10.0 | 8 | 2.27 | 781 | 24" X65 (6.5 mm) | 300 | 1.83 |
| BNBC Zone 1, M 7.5 at 30 km | 10.0 | 8 | 2.27 | 781 | 30" X70 (8.0 mm) | 0 | 0.0 |
| BNBC Zone 1, M 7.5 at 60 km | 10.0 | 3 | 0.46 | 293 | 24" X65 (6.5 mm) | 0 | 0.0 |
| BNBC Zone 1, M 7.5 at 60 km | 10.0 | 3 | 0.46 | 293 | 30" X70 (8.0 mm) | 0 | 0.0 |
| BNBC Zone 1, M 7.5 at 60 km | 10.0 | 5 | 0.46 | 488 | 24" X65 (6.5 mm) | 0 | 0.0 |
| BNBC Zone 1, M 7.5 at 60 km | 10.0 | 5 | 0.46 | 488 | 30" X70 (8.0 mm) | 0 | 0.0 |
| BNBC Zone 1, M 7.5 at 60 km | 10.0 | 8 | 0.46 | 781 | 24" X65 (6.5 mm) | 0 | 0.0 |
| BNBC Zone 1, M 7.5 at 60 km | 10.0 | 8 | 0.46 | 781 | 30" X70 (8.0 mm) | 0 | 0.0 |
| BNBC Zone 1, M 7.5 at 100 km | 10.0 | 3 | 0.08 | 32 | 24" X65 (6.5 mm) | 0 | 0.0 |
| BNBC Zone 1, M 7.5 at 100 km | 10.0 | 3 | 0.08 | 32 | 30" X70 (8.0 mm) | 0 | 0.0 |
| BNBC Zone 1, M 7.5 at 100 km | 10.0 | 5 | 0.08 | 53 | 24" X65 (6.5 mm) | 0 | 0.0 |
| BNBC Zone 1, M 7.5 at 100 km | 10.0 | 5 | 0.08 | 53 | 30" X70 (8.0 mm) | 0 | 0.0 |
| BNBC Zone 1, M 7.5 at 100 km | 10.0 | 8 | 0.08 | 85 | 24" X65 (6.5 mm) | 0 | 0.0 |
| BNBC Zone 1, M 7.5 at 100 km | 10.0 | 8 | 0.08 | 85 | 30" X70 (8.0 mm) | 0 | 0.0 |
| Distant great earthquake, M 8.0 at 200 km | 0.0 | 3 | 0.0 | - | no spread | - | - |
| Distant great earthquake, M 8.0 at 200 km | 0.0 | 5 | 0.0 | - | no spread | - | - |
| Distant great earthquake, M 8.0 at 200 km | 0.0 | 8 | 0.0 | - | no spread | - | - |

Extra HDD CAPEX assumes the base design surfaces at the bank toes and extends both
banks of the 4 main crossings by the setback, at the placeholder HDD rate.
For comparison, the base lifecycle cost of 24" X65 is US$ 83.2M.

## Flotation in liquefied flood plain (empty pipe)

| Design | Net uplift kN/m | Concrete coating to hold down mm (liquefied) | (water only) mm |
|---|---|---|---|
| 24" X65 | 4.4 | 423 | 79 |
| 30" X70 | 6.9 | 529 | 100 |

## Reading the results

* **Liquefaction is likely in the loose deltaic sands** at the BNBC Zone 1 design level
  (LPI about 10, high). The distant great earthquake does not trigger it with this
  profile.
* **Lateral spread depends strongly on the source distance**, which the code zoning does
  not give. At 30 km a 5 m bank spreads about 2 m and the movement reaches about 500 m
  inland; at 100 km the spread is much smaller. A site-specific seismic hazard study
  (deaggregation by magnitude and distance) is therefore the first data need.
* **Bank height sets how far the spread reaches, not the movement at the toe.** Near the
  face the regression's W term is capped at the edge of its data (20 %), so the toe
  value is the same for 3, 5 and 8 m banks. Taller banks spread further inland and drag
  more pipe. The required HDD setback for 24" X65 therefore grows from 0 (3 m bank) to
  50 m (5 m) and 300 m (8 m) at the 30 km scenario. No setback is needed at 60 km or
  beyond.
* **The remedy is HDD length, not pipe size.** In the worst case tested (8 m banks, 30 km)
  the longer HDDs add about US$ 1.8M (about 2 % of lifecycle cost). 24" X65 plus that
  extension is still well below 30" X70. The 30" (8 mm wall) needs no setback, but costs
  US$ 14M more over its life.
* **Flotation.** An empty pipe in liquefied soil needs far more ballast than in water.
  Where open-cut trench crosses liquefiable flood plain, use set-on weights or screw
  anchors, or keep the pipe below the liquefiable layer, rather than the concrete
  coating sized for water.
* **Data needed:**
  * SPT or CPT boreholes at each main crossing and in the flood plain (N, fines, D50,
    and PI of the crust);
  * bank survey (height, slope, distance to the river channel);
  * a site-specific seismic hazard study for the route.

Runtime 94.4 s. Reproduce:
`uv run python studies/bhola_khulna/liquefaction_case.py`.
