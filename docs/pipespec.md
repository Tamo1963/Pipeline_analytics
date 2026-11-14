# PipeSpec Library

The pipe specification library behind every design iteration. It is read-only, loaded from version-controlled YAML in `src/pipeline_analytics/pipespec/data/`.

## Coverage (Phase 1 MVP)

| Data | Source | Content |
|---|---|---|
| Steel grades | API 5L 46th ed. / ISO 3183:2019 | PSL1: A25, A25P, A, B, X42–X70. PSL2: B–X120 with delivery conditions (R/N/Q/M), SMYS/SMTS min & max, Y/T max |
| Steel grades | IS 3589 | Fe330, Fe410, Fe450 (water/sewage service only) |
| Pipe sizes | ASME B36.10M | NPS ½–48 (33 sizes): DN, OD (in, mm), designated wall thicknesses (Sch 10–160, STD, XS, XXS) |
| Plain-end weight | Calculated | `0.0246615 (D − t) t` kg/m, steel density 7850 kg/m³ |
| Manufacturing processes | API 5L pipe types | SMLS, HFW (ERW), SAWL (LSAW), SAWH (HSAW): **indicative** OD/WT ranges, joint factor E = 1.0 |
| Coatings | ISO 21809-1/-2/-5 | 3LPE, 3LPP, FBE, CWC: **indicative** temperature limits |

**Not yet included (Phase 1b backlog):** API 5L chemistry, CE (IIW/Pcm) and toughness (CVN/DWTT) requirements; dimensional tolerances; non-designated B36.10M intermediate thicknesses; ASME B36.19M; CSA Z245.1; EN 10208-2; ASTM A106/A53/A333/A312/A671/A672; IS 1239; Indian mill capability lists.

## Data provenance and verification

* Values are factual data (strengths, dimensions) with a table reference (`ref`). No text from the standards is reproduced.
* Every grade record has `verified: false`. The seed values were compiled from widely published data. Before a study is issued:
  1. Check each record against your organisation's licensed copy of the standard edition named in `standards.yaml`.
  2. Set `verified: true` in the YAML and commit with the checker's name in the commit message.
* Process OD/WT ranges and coating temperature limits are screening values. Replace them with actual mill and coating-applicator data for each project.

Automated checks (`tests/pipespec/`) catch transcription errors:

* all NPS ½–48 present; OD in/mm agree; DN and OD ascending;
* per size: wall thicknesses ascending and unique, schedule numbers rise with thickness, STD < XS < XXS;
* API 5L: ISO name number = SMYS; X-grade number ≈ ksi (within 1.5 %); strength rises with grade; PSL1 and PSL2 minimums agree; PSL2 SMYS/SMTS ≤ Y/T max;
* spot values (e.g. NPS 24 STD = 9.53 mm, 141.12 kg/m) and a full independent check of every weight against the inch formula `10.69 (D − t) t` lb/ft.

## Python API

```python
from pipeline_analytics.pipespec import default_library, WtPolicy

lib = default_library()

lib.grade("X70M")  # PSL2 X70; also "X70", "L485", "L485M/X70M"
lib.grade("X52", psl=1)
lib.find_grades(psl=2, min_smys=450)  # X65 ... X120
lib.find_grades(psl=2, condition="N")  # normalized grades: B ... X60

size = lib.size(24)  # or lib.size("1-1/4"), lib.size(dn=600), lib.size_by_od(610)
size.wt("STD").wt_mm  # 9.53
size.weight_kg_m(12.7)  # 187.08

lib.next_wall_thickness(size, 11.2)  # 12.7 (next designated WT)
lib.next_wall_thickness(size, 11.23, WtPolicy.INCREMENT)  # 11.3 (0.1 mm mill step)

lib.processes_for(od_mm=508, wt_mm=30)  # SMLS, SAWL
lib.coatings_for(design_temp_c=95)  # 3LPP

# PSL2 grades >= X65 as HSAW at NPS 36 with WT <= 15.9 mm
lib.find(36, min_grade="X65", process="SAWH", max_wt_mm=15.9)
```

## Which wall-thickness policy to use

* `WtPolicy.SCHEDULE` rounds up to the next ASME B36.10M designated thickness. Use it for station piping and small-bore SMLS/HFW line pipe.
* `WtPolicy.INCREMENT` rounds up to the next mill step (default 0.1 mm). Use it for plate- or coil-based LSAW/HSAW line pipe, where the mill can supply almost any thickness. Large-diameter sizes (NPS 38–48) only have STD and XS designated, so this is the normal policy there.

## Command line

```bash
uv run pipespec grades --psl 2 --min-smys 450
```

```bash
uv run pipespec grade L485M
```

```bash
uv run pipespec sizes
```

```bash
uv run pipespec wt 24
```

```bash
uv run pipespec next-wt 24 11.23 --policy increment
```

```bash
uv run pipespec find 36 --min-grade X65 --process SAWH --max-wt 15.9
```
