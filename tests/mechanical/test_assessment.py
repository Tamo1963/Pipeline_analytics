from typing import Any

import pytest

from pipeline_analytics.core.jurisdiction import Service
from pipeline_analytics.mechanical import (
    BuoyancyInput,
    LocationClass,
    PgdInput,
    PipeSection,
    SegmentInput,
    SeismicInput,
    Soil,
    assess_segment,
)


def base(soil: Soil, **kw: Any) -> SegmentInput:
    return SegmentInput(service=Service.GAS, design_pressure_pa=92e5, cover_m=1.2, soil=soil, **kw)


def test_core_checks_only(pipe24: PipeSection, dense_sand: Soil) -> None:
    a = assess_segment(pipe24, base(dense_sand))
    assert len(a.checks) == 5
    assert a.passed
    assert a.governing.name == "hoop stress"
    assert a.buoyancy is None and a.wave_propagation is None and a.pgd is None
    # anchor length uses the axial soil spring
    assert a.thermal.virtual_anchor_length_m == pytest.approx(
        abs(a.thermal.axial_force_n) / a.springs.axial.max_force_n_per_m
    )


def test_all_checks(pipe24: PipeSection, dense_sand: Soil) -> None:
    a = assess_segment(
        pipe24,
        base(
            dense_sand,
            buoyancy=BuoyancyInput(),
            seismic=SeismicInput(pgv_m_s=0.5, pga_m_s2=3.0, wavelength_m=1000),
            pgd=PgdInput(ground_displacement_m=1.0, block_length_m=100),
        ),
    )
    assert len(a.checks) == 10
    assert not a.passed
    assert a.governing.name == "negative buoyancy"
    assert "FAIL negative buoyancy" in a.summary()


def test_class_change_moves_governing_check(pipe24: PipeSection, dense_sand: Soil) -> None:
    a = assess_segment(pipe24, base(dense_sand, location_class=LocationClass.CLASS_3))
    assert a.governing.name == "hoop stress"
    assert not a.passed
