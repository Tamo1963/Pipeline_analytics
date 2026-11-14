import math

import pytest

from pipeline_analytics.core.units import Q_
from pipeline_analytics.mechanical import (
    PerformanceLevel,
    PipeSection,
    WaveType,
    longitudinal_pgd,
    wave_propagation,
)
from pipeline_analytics.mechanical.seismic import compressive_strain_limit, g_to_ms2


def test_compressive_limit_operability(pipe24: PipeSection) -> None:
    # hoop/E = 294 MPa / 207 GPa exceeds the 0.4 SMYS/E cap
    cap = 0.4 * 485e6 / 207e9
    expected = 0.5 * 0.00953 / 0.610 - 0.0025 + 3000 * cap**2
    assert compressive_strain_limit(pipe24, 92e5) == pytest.approx(expected)
    low = 20e5 * 0.610 / (2 * 207e9 * 0.00953)
    assert compressive_strain_limit(pipe24, 20e5) == pytest.approx(
        0.5 * 0.00953 / 0.610 - 0.0025 + 3000 * low**2
    )


def test_compressive_limit_integrity(pipe24: PipeSection) -> None:
    assert compressive_strain_limit(
        pipe24, level=PerformanceLevel.PRESSURE_INTEGRITY
    ) == pytest.approx(1.76 * 0.00953 / 0.610)


def test_ovality_reduces_limit(pipe24: PipeSection) -> None:
    assert compressive_strain_limit(pipe24, ovality_d_min=0.600) < compressive_strain_limit(pipe24)


def test_wave_propagation_hand_calc(pipe24: PipeSection) -> None:
    r = wave_propagation(pipe24, 0.5, Q_(0.3, "g0"))
    assert r.ground_strain == pytest.approx(0.5 / (2 * 2000))
    assert r.ground_curvature_per_m == pytest.approx(g_to_ms2(0.3) / 2000**2)
    assert r.pipe_total_strain == pytest.approx(r.ground_strain + r.ground_curvature_per_m * 0.305)
    assert not r.friction_limited
    assert all(c.passed for c in r.checks)


def test_rayleigh_wave_doubles_axial_strain(pipe24: PipeSection) -> None:
    s = wave_propagation(pipe24, 0.5, 3.0).ground_strain
    r = wave_propagation(pipe24, 0.5, 3.0, wave_type=WaveType.RAYLEIGH).ground_strain
    assert r == pytest.approx(2 * s)


def test_wave_friction_limit(pipe24: PipeSection) -> None:
    r = wave_propagation(
        pipe24, 1.5, 3.0, apparent_velocity=300, axial_soil_resistance=14514, wavelength=100
    )
    cap = 14514 * 100 / (4 * pipe24.steel_area_m2 * 207e9)
    assert r.friction_limited
    assert r.pipe_axial_strain == pytest.approx(cap)


def test_longitudinal_pgd(pipe24: PipeSection) -> None:
    ae = pipe24.steel_area_m2 * 207e9
    r = longitudinal_pgd(pipe24, 1.0, 100, 14514)
    assert r.strain_displacement_controlled == pytest.approx(math.sqrt(2 * 1.0 * 14514 / ae))
    assert r.strain_length_controlled == pytest.approx(14514 * 100 / (2 * ae))
    assert r.governing == "block length"
    assert r.elastic_valid
    small = longitudinal_pgd(pipe24, 0.01, 5000, 14514)
    assert small.governing == "displacement"


def test_long_block_flags_inelastic(pipe24: PipeSection) -> None:
    r = longitudinal_pgd(pipe24, 3.0, 2000, 30000)
    assert not r.elastic_valid
