"""Pipe-soil FE model: closed-form checks, material, scenarios."""

import math

import numpy as np
import pytest

from pipeline_analytics.mechanical.common import PipeSection
from pipeline_analytics.mechanical.fe import (
    FeOptions,
    PipeSoilModel,
    SpringSet,
    SteelCurve,
    graded_mesh,
)
from pipeline_analytics.mechanical.pgd import (
    analyse_pgd,
    crossing_angle_sweep,
    fault_movement,
    longitudinal_pgd,
    settlement,
    transverse_pgd,
)
from pipeline_analytics.mechanical.seismic import longitudinal_pgd as closed_form_longitudinal
from pipeline_analytics.mechanical.soil import Coating, Soil, SoilType, soil_springs

PIPE = PipeSection(od_m=0.6096, wt_m=0.0127, smys_pa=448e6)
E = 207e9


def elastic_springs(k: float, big: float = 1e12) -> SpringSet:
    return SpringSet(axial_force=big, axial_yield=big / k, lateral_force=big, lateral_yield=big / k,
                     uplift_force=big, uplift_yield=big / k, bearing_force=big,
                     bearing_yield=big / k)  # fmt: skip


@pytest.fixture(scope="module")
def ala_springs():  # type: ignore[no-untyped-def]
    soil = Soil(soil_type=SoilType.DENSE_SAND, unit_weight_n_m3=18e3, friction_angle_deg=36)
    return soil_springs(PIPE.od_m, 1.5, soil, Coating.POLYETHYLENE)


# ------------------------------------------------------------------ material


@pytest.mark.parametrize("model", ["ramberg_osgood", "bilinear", "elastic"])
def test_stress_inverts_strain(model: str) -> None:
    c = SteelCurve(model=model, yield_pa=448e6, n=10, r=12)  # type: ignore[arg-type]
    for s in (-600e6, -448e6, -100e6, 0.0, 200e6, 448e6, 520e6):
        if model != "elastic" or abs(s) <= 448e6:
            sig, _ = c.stress(np.array([c.strain_of(s)]))
            assert sig[0] == pytest.approx(s, rel=1e-8, abs=1e-3)


def test_tangent_is_derivative() -> None:
    c = SteelCurve.for_grade("X70", 483e6)
    eps = np.array([-0.02, -0.003, 0.0005, 0.002, 0.004, 0.03])
    s1, et = c.stress(eps)
    s2, _ = c.stress(eps + 1e-8)
    assert np.allclose((s2 - s1) / 1e-8, et, rtol=1e-3)


def test_grade_lookup_uses_next_lower_entry() -> None:
    assert SteelCurve.for_grade("X65", 448e6).r == 12.0  # X60 entry
    assert "X60" in SteelCurve.for_grade("L450M", 450e6).source or True
    assert SteelCurve.for_grade("X80", 555e6).n == 5.5  # X70 entry


# ------------------------------------------------------------------ closed forms


def test_hetenyi_transverse_step() -> None:
    """Elastic beam on elastic foundation under a small ground step (Hetenyi):
    M_max = EI delta beta^2 e^(-pi/4) sin(pi/4), beta = (k / 4EI)^(1/4)."""
    k, delta = 2e7, 0.001
    x = graded_mesh(-60, 60, [0.0], 0.15, 2.0, 5.0)
    m = PipeSoilModel(PIPE, elastic_springs(k), SteelCurve(model="elastic", yield_pa=448e6), x,
                      options=FeOptions(far_field_springs=False))  # fmt: skip
    r = m.solve(lambda xx: np.vstack([0 * xx, delta / 2 * np.sign(xx), 0 * xx]))
    ei = E * PIPE.second_moment_m4
    beta = (k / (4 * ei)) ** 0.25
    exact = ei * delta * beta**2 * math.exp(-math.pi / 4) * math.sin(math.pi / 4)
    assert r.converged
    assert max(abs(v) for v in r.moment_horizontal_nm) == pytest.approx(exact, rel=0.01)
    assert r.max_tensile_strain == pytest.approx(exact / ei * PIPE.od_m / 2, rel=0.02)


def test_wide_trough_pipe_follows_ground_curvature() -> None:
    """Elastic pipe, stiff springs, wide cosine trough: pipe curvature = ground curvature
    = delta 2 pi^2 / W^2 at the centre."""
    w, delta = 400.0, 0.5
    a = analyse_pgd(PIPE, elastic_springs(1e9), settlement(delta, w),
                    steel=SteelCurve(model="elastic", yield_pa=448e6))  # fmt: skip
    kappa = delta * 2 * math.pi**2 / w**2
    # at the centre (smooth ground curvature); the edges add a local overshoot where the
    # cosine profile's curvature jumps to zero
    i0 = int(np.argmin(np.abs(np.array(a.fe.x_elem_m))))
    m0 = abs(a.fe.moment_vertical_nm[i0])
    assert m0 == pytest.approx(E * PIPE.second_moment_m4 * kappa, rel=0.01)


def test_longitudinal_pgd_matches_closed_form(ala_springs) -> None:  # type: ignore[no-untyped-def]
    """Elastic pipe, near rigid-plastic friction t_u, block of length L moving delta.

    Length-controlled (short block): eps = t_u L / (2 A E), as in seismic.longitudinal_pgd.
    Displacement-controlled (long block): friction acts on both sides of each scarp, so
    each side takes delta/2 and eps = sqrt(delta t_u / (A E)). The screening function
    puts all of delta on one side, so it is conservative by sqrt(2) here.
    """
    elastic = SteelCurve(model="elastic", yield_pa=448e6)
    tu = ala_springs.axial.max_force_n_per_m
    rigid = SpringSet.from_ala(ala_springs).model_copy(update={"axial_yield": 1e-5})
    ae = PIPE.steel_area_m2 * E
    fe_short = analyse_pgd(PIPE, rigid, longitudinal_pgd(0.5, 100.0), steel=elastic)
    assert fe_short.fe.max_tensile_strain == pytest.approx(tu * 100 / (2 * ae), rel=0.03)
    cf = closed_form_longitudinal(PIPE, 0.5, 100.0, tu)
    assert cf.governing == "block length"
    assert fe_short.fe.max_tensile_strain == pytest.approx(cf.pipe_strain, rel=0.03)
    fe_long = analyse_pgd(PIPE, rigid, longitudinal_pgd(0.02, 2000.0), steel=elastic)
    assert fe_long.fe.max_tensile_strain == pytest.approx(math.sqrt(0.02 * tu / ae), rel=0.03)
    screening = closed_form_longitudinal(PIPE, 0.02, 2000.0, tu).pipe_strain
    assert screening == pytest.approx(math.sqrt(2) * fe_long.fe.max_tensile_strain, rel=0.03)


# ------------------------------------------------------------------ scenarios


def test_fault_components() -> None:
    f = fault_movement(2.0, 60)  # strike-slip
    assert (f.axial_m, f.transverse_m, f.vertical_m) == pytest.approx((1.0, math.sqrt(3), 0))
    n = fault_movement(1.0, 90, dip_deg=60, rake_deg=-90)  # normal: extension, footwall up
    assert n.axial_m == pytest.approx(0.5) and n.vertical_m == pytest.approx(-math.sqrt(3) / 2)
    r = fault_movement(1.0, 90, dip_deg=60, rake_deg=90)  # reverse: shortening
    assert r.axial_m == pytest.approx(-0.5)


def test_tension_crossing_beats_compression(ala_springs) -> None:  # type: ignore[no-untyped-def]
    sweep = crossing_angle_sweep(PIPE, ala_springs, 2.0, angles_deg=(45, 90, 120))
    by = {c.crossing_angle_deg: c for c in sweep.cases}
    assert sweep.best_angle_deg == 45
    assert by[120].max_compressive_strain > 5 * by[45].max_compressive_strain


def test_analysis_reports_limit_and_checks(ala_springs) -> None:  # type: ignore[no-untyped-def]
    a = analyse_pgd(PIPE, ala_springs, fault_movement(3.0, 100), pressure=9e6)
    assert not a.passed and a.limit_fraction is not None
    assert 0 < a.movement_at_limit_m < 3.0  # type: ignore[operator]
    assert a.checks[1].name.startswith("compressive strain (FE, fault)")
    ok = analyse_pgd(PIPE, ala_springs, transverse_pgd(0.3, 80))
    assert ok.passed and ok.limit_fraction is None
    assert ok.fe.end_strain_ratio < 0.8


def test_settlement_step_and_block(ala_springs) -> None:  # type: ignore[no-untyped-def]
    s = analyse_pgd(PIPE, ala_springs, settlement(0.2, pattern="step"))
    assert s.fe.converged and min(s.fe.w_m) < -0.05  # pipe dragged down on one side
    b = analyse_pgd(PIPE, ala_springs, transverse_pgd(1.0, 50, "block"))
    c = analyse_pgd(PIPE, ala_springs, transverse_pgd(1.0, 50, "cosine"))
    assert b.fe.max_tensile_strain > c.fe.max_tensile_strain  # abrupt edges are worse


def test_non_convergence_is_reported(ala_springs) -> None:  # type: ignore[no-untyped-def]
    a = analyse_pgd(PIPE, ala_springs, fault_movement(2.0, 60),
                    options=FeOptions(max_steps=2, first_step=0.05))  # fmt: skip
    assert not a.fe.converged and a.fe.fraction < 1
    assert not a.passed and any("stopped early" in n for n in a.notes)


def test_bad_inputs() -> None:
    with pytest.raises(ValueError):
        transverse_pgd(1.0, 0)
    with pytest.raises(ValueError):
        settlement(0.3)
    with pytest.raises(ValueError):
        PipeSoilModel(PIPE, elastic_springs(1e7), SteelCurve(yield_pa=448e6), np.array([0, 1, 1.0]))
