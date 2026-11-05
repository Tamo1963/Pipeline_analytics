import math

import pytest

from pipeline_analytics.mechanical import PipeSection, Restraint, thermal_stress
from pipeline_analytics.mechanical.thermal import EquivalentStress

SH = 92e5 * 0.610 / (2 * 0.00953)
EAT = 207e9 * 11.7e-6


def test_restrained_hand_calc(pipe24: PipeSection) -> None:
    r = thermal_stress(pipe24, 92e5, 25, 50, axial_soil_resistance=14514)
    s_l = 0.3 * SH - EAT * 25
    assert r.longitudinal_stress_pa == pytest.approx(s_l)
    assert r.equivalent_stress_pa == pytest.approx(math.sqrt(SH**2 - SH * s_l + s_l**2))
    assert r.axial_force_n == pytest.approx(pipe24.steel_area_m2 * s_l)
    assert r.virtual_anchor_length_m == pytest.approx(abs(r.axial_force_n) / 14514)
    assert all(c.passed for c in r.checks)


def test_hot_line_goes_into_compression(pipe24: PipeSection) -> None:
    r = thermal_stress(pipe24, 0, 20, 80)
    assert r.longitudinal_stress_pa == pytest.approx(-EAT * 60)
    assert r.axial_force_n < 0
    assert r.virtual_anchor_length_m is None


def test_unrestrained(pipe24: PipeSection) -> None:
    r = thermal_stress(pipe24, 92e5, 25, 80, Restraint.UNRESTRAINED)
    assert r.longitudinal_stress_pa == pytest.approx(SH / 2)
    assert r.axial_force_n == 0.0


def test_tresca_at_least_von_mises(pipe24: PipeSection) -> None:
    vm = thermal_stress(pipe24, 92e5, 25, 60).equivalent_stress_pa
    tr = thermal_stress(pipe24, 92e5, 25, 60, criterion=EquivalentStress.TRESCA)
    # both principal stresses tensile -> Tresca = largest = hoop
    assert tr.equivalent_stress_pa == pytest.approx(SH)
    assert tr.equivalent_stress_pa >= vm
    # hot line: longitudinal compressive -> Tresca = S_h - S_L
    hot = thermal_stress(pipe24, 92e5, 0, 100, criterion=EquivalentStress.TRESCA)
    assert hot.equivalent_stress_pa == pytest.approx(SH - (0.3 * SH - EAT * 100))


def test_bending_adds_with_worse_sign(pipe24: PipeSection) -> None:
    base = thermal_stress(pipe24, 0, 20, 80).longitudinal_stress_pa
    bent = thermal_stress(pipe24, 0, 20, 80, bending_stress=50e6).longitudinal_stress_pa
    assert bent == pytest.approx(base - 50e6)


def test_large_temperature_rise_fails(pipe24: PipeSection) -> None:
    r = thermal_stress(pipe24, 92e5, 0, 200)
    assert not all(c.passed for c in r.checks)
