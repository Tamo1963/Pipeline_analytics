# 0010: Pipe-soil FE model for ground movement: moderate-rotation beam on ALA springs

**Status:** Accepted (2026-11-05)

## Context
Phase 2c (plan §5, item 2c) requires fault crossings, transverse PGD and settlement with the ALA soil springs. Closed-form methods cover only parts of this:
* Kennedy et al. and Karamitros et al. cover strike-slip faults in tension.
* O'Rourke's formulas cover longitudinal PGD.

Commercial FE (ABAQUS shell models) is the industry reference, but it cannot run inside the screening loop or the bot.

## Decision
* Write our own beam model in NumPy/SciPy. Use a total-Lagrangian, moderate-rotation (von Kármán) formulation with element-averaged membrane strain, so axial tension from transverse offsets is captured without membrane locking.
* Use fiber sections with path-independent steel curves (Ramberg–Osgood, bilinear or elastic). Ground movement is monotonic, so cyclic plasticity is not needed.
* Use elastic-perfectly-plastic ALA springs on the displacement of the pipe relative to the ground, with an analytical far-field axial spring at each end so models stay short (±300–500 m).
* Use Newton–Raphson with load stepping, a secant predictor and a line search. Report peak outer-fiber strains and the movement at which ALA limits are first reached.
* Verify against closed forms (Hetényi, ground-following curvature, friction-controlled longitudinal PGD). Benchmark against Karamitros et al. (2007), whose analytical method is implemented as an independent cross-check.

## Consequences
* Each analysis takes seconds, so crossing-angle sweeps and bot calls are practical.
* Local buckling, ovalisation and large rotation (above about 0.3 rad) are outside the model. They are judged by strain limits and flagged in notes. Detailed design of a fault crossing still needs a shell model.
* The Ramberg–Osgood parameters by grade come from published tables and are marked "verify", like the other ALA defaults.
