# Phase 2c: pipe-soil finite-element model for ground movement

Fault crossings, transverse PGD (lateral spread or landslide), settlement and longitudinal PGD, using the ALA 2001 soil springs from `mechanical/soil.py`. See ADR 0010 for the design choices.

## Model (`mechanical/fe.py`)

| Item | Choice |
|---|---|
| Pipe | 3D beam along x. Each node has five degrees of freedom: axial u, horizontal v and v′, vertical w and w′. Torsion is not loaded. |
| Kinematics | Total-Lagrangian, moderate rotation (von Kármán). Membrane strain is u′ + (v′² + w′²)/2, which captures the cable tension that governs fault crossings. A note is raised if any rotation exceeds 0.3 rad. |
| Element | Linear u, cubic Hermite v and w, three Gauss points. Membrane strain is averaged over each element, which avoids membrane locking. |
| Section | 24 fibers on the ring of exact second moment. Strains are reported at the outer surface. |
| Steel | Ramberg–Osgood (ASCE 1984 / ALA 2001 parameters by grade; **verify**), bilinear or elastic. Optional initial axial stress (e.g. restrained thermal plus Poisson). |
| Soil | Elastic-perfectly-plastic springs lumped at the nodes (axial, lateral, uplift, bearing), acting on the displacement of the pipe relative to the ground. |
| Ends | The ends follow the ground, with zero slope. An axial far-field spring F = √(2·A·E·T_u·δ) at each end represents the semi-infinite pipe beyond the model. A ±500 m model then matches a ±3 km fixed-end model to 0.15 %. |
| Solution | Newton–Raphson on the ground-movement fraction, with a secant predictor, adaptive steps and a backtracking line search. A typical case takes 0.3–3 s for 250–500 elements. |
| Mesh | Graded: about D/2 within 20·D of a fault or zone edge, growing to 10 m. |

**Not modelled:** local buckling, ovalisation and cyclic loading. Compressive strain is judged against the ALA local-buckling strain limit. Beyond that limit the beam model no longer represents the pipe, and the analysis says so.

## Scenarios (`mechanical/pgd.py`)

| Function | Ground field |
|---|---|
| `fault_movement(slip, crossing_angle, dip, rake)` | Abrupt offset at x = 0, half applied to each side. Rake: 0 = strike-slip, 90 = reverse, −90 = normal. β < 90° with strike-slip stretches the pipe. |
| `transverse_pgd(δ, W, "cosine" or "block")` | Horizontal movement of a zone of width W |
| `settlement(δ, W)` / `settlement(δ, pattern="step")` | Cosine trough, or differential settlement |
| `longitudinal_pgd(δ, L)` | Block of length L moving along the pipe |

`analyse_pgd(pipe, springs, movement, grade=..., pressure=..., level=...)` returns the following:

* the FE result: strain, axial force, moments and displacements along the pipe, and the history of peak strain against movement;
* ALA strain checks for the chosen performance level, with pressure entering the compressive limit;
* the movement at which a strain limit is first reached;
* validity notes.

`crossing_angle_sweep` returns the strain utilisation for a range of crossing angles and the best angle.

**Where it appears:**
* **Streamlit:** Mechanical tab, "Ground movement" expander, with a crossing-angle sweep.
* **Bot:** the `ground_movement_check` tool.

## Verification (`tests/mechanical/test_fe.py`, `test_fe_benchmarks.py`)

### Closed forms

| Case | Reference | Agreement |
|---|---|---|
| Elastic beam on elastic foundation, small transverse step | Hetényi: M_max = EI·Δ·β²·e^(−π/4)·sin(π/4) | moment within 1 %, strain within 2 % |
| Wide settlement trough, stiff springs, elastic pipe | Pipe follows ground curvature 2π²δ/W² | within 1 % at the centre |
| Longitudinal PGD, short block | T_u·L/(2AE) (also `seismic.longitudinal_pgd`) | within 3 % |
| Longitudinal PGD, long block | √(δ·T_u/(AE)): friction on both sides of each scarp | within 3 % |
| Ramberg–Osgood inversion and tangent | Analytical ε(σ) and its derivative | 1e-8 and 1e-3 |

The long-block case shows that the screening formula `seismic.longitudinal_pgd` puts the whole relative movement on one side of the scarp. That makes it conservative by √2 where the strain is displacement-controlled.

### Published benchmark: Karamitros, Bouckovalas & Kouretzis (2007)

*Stress analysis of buried steel pipelines at strike-slip fault crossings*, Soil Dyn. Earthquake Eng. 27, 200–211.

**Case definition:**
* Pipe: 36" × 11.9 mm API 5L X65, bilinear steel (σ₁ = 490 MPa, E₁ = 210 GPa, E₂ = 1.088 GPa).
* Springs (their Table 3):
  * axial: 40.5 kN/m at 3 mm;
  * lateral: 318.6 kN/m at 11.4 mm;
  * uplift: 52 kN/m at 2.2 mm;
  * bearing: 1360 kN/m at 100 mm.
* Load: strike-slip offsets of 0.5–2·D at β = 30°, 45° and 60°.

The paper gives its 3D shell-FE results only as plots. It states that its analytical method agrees with those results within about 10 %. That method is implemented in `mechanical/fault_analytical.py`, and our FE is compared with it:

| β | Δf / D | Axial force at fault, published method (MN) | Axial force, FE (MN) | Peak strain, published method | Peak strain, FE | FE / published |
|---|---|---|---|---|---|---|
| 30° | 0.5 | 10.66 | 10.59 | 0.52 % | 0.54 % | 1.03 |
| 30° | 1 | 15.07 | 14.78 | 1.50 % | 1.62 % | 1.08 |
| 30° | 1.5 | 17.04 | 17.07 | 2.38 % | 2.35 % | 0.99 |
| 30° | 2 | 17.42 | 17.45 | 3.40 % | 3.36 % | 0.99 |
| 45° | 0.5 | 9.63 | 9.56 | 0.57 % | 0.62 % | 1.08 |
| 45° | 1 | 13.62 | 13.37 | 1.38 % | 1.64 % | 1.19 |
| 45° | 1.5 | 16.63 | 16.22 | 1.77 % | 1.87 % | 1.06 |
| 45° | 2 | 17.16 | 17.23 | 2.73 % | 2.76 % | 1.01 |
| 60° | 0.5 | 8.10 | 8.05 | 0.55 % | 0.60 % | 1.09 |
| 60° | 1 | 11.45 | 11.29 | 1.21 % | 1.55 % | 1.28 |
| 60° | 1.5 | 14.03 | 13.82 | 1.66 % | 1.90 % | 1.15 |
| 60° | 2 | 16.20 | 15.94 | 1.79 % | 1.90 % | 1.06 |

**Reading the table:**
* **Axial force at the fault:** within 2.5 % in every case. The cable mechanism and the friction-anchored elongation are reproduced.
* **Peak strain:** within 10 % in 9 of the 12 cases.
* **The three remaining cases (β = 45° and 60° at Δf ≈ 1–1.5·D):** the FE is 15–28 % higher than the analytical method. The paper also places its largest analytical-versus-numerical differences in this intermediate range.

The FE result is mesh-converged at β = 60°, Δf = 1·D: peak strain changes by under 0.5 % for element sizes from 0.4 m to 0.05 m, 24 or 48 fibers, and model lengths of ±500 m or ±1 km.

**Conclusion:** the FE matches the published method where it is accurate, and is conservative where the method is approximate. The tests lock in this behaviour: force within 5 %, strain within the tolerances shown, and the FE never more than 5 % below the published method.

## Using the results
* This is a screening tool. Final fault-crossing design needs project-specific soil data, a calibrated steel curve and usually a shell model of the crossing zone.
* Pipes in tension at strike-slip faults (β < 90°) perform far better than pipes in compression. Use the crossing-angle sweep when routing near mapped faults.
