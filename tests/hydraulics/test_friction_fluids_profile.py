import math

import pytest
from pydantic import ValidationError

from pipeline_analytics.hydraulics import (
    Gas,
    Liquid,
    Profile,
    colebrook,
    darcy_friction,
    swamee_jain,
)


def test_laminar() -> None:
    assert darcy_friction(1000, 1e-4) == pytest.approx(0.064)
    with pytest.raises(ValueError):
        darcy_friction(0, 1e-4)


def test_colebrook_satisfies_equation() -> None:
    for re, rr in [(1e4, 0), (1e5, 1e-4), (1e7, 7.7e-5)]:
        f = colebrook(re, rr)
        rhs = -2 * math.log10(rr / 3.7 + 2.51 / (re * math.sqrt(f)))
        assert 1 / math.sqrt(f) == pytest.approx(rhs, rel=1e-9)


def test_colebrook_smooth_pipe_moody_value() -> None:
    # smooth pipe, Re 1e5: f ~ 0.0180 (Moody chart; Blasius gives 0.0178)
    assert colebrook(1e5, 0.0) == pytest.approx(0.0180, rel=0.01)


def test_swamee_jain_close_to_colebrook() -> None:
    for re in (5e3, 1e5, 1e6, 1e8):
        for rr in (1e-6, 1e-4, 1e-3):
            assert swamee_jain(re, rr) == pytest.approx(colebrook(re, rr), rel=0.03)


def test_gas_standard_density() -> None:
    # air at 15 degC, 1 atm = 1.225 kg/m3; SG 0.6 -> ~0.735
    assert Gas().std_density == pytest.approx(0.6 * 1.225, rel=0.005)


def test_papay_z() -> None:
    g = Gas()
    t_pc, p_pc = g.pseudo_critical()
    assert t_pc == pytest.approx((168 + 325 * 0.6 - 12.5 * 0.36) * 5 / 9)
    pr, tr = 1.5, 1.5
    expected = 1 - 3.52 * pr / 10 ** (0.9813 * tr) + 0.274 * pr**2 / 10 ** (0.8157 * tr)
    assert g.z(pr * p_pc, tr * t_pc) == pytest.approx(expected)
    assert 0.83 < expected < 0.88  # Standing-Katz chart ~0.86
    assert g.z(101_325) == pytest.approx(1.0, abs=0.005)


def test_liquid_dynamic_viscosity() -> None:
    assert Liquid(density_kg_m3=850, kinematic_viscosity_m2_s=1e-5).dynamic_viscosity_pa_s == (
        pytest.approx(8.5e-3)
    )


def test_profile_validation_and_resample() -> None:
    with pytest.raises(ValidationError):
        Profile(chainage_m=(0, 10, 5), elevation_m=(0, 0, 0))
    with pytest.raises(ValidationError):
        Profile(chainage_m=(1, 10), elevation_m=(0, 0))
    p = Profile(chainage_m=(0, 1500, 4000), elevation_m=(0, 300, 100)).resample(1000)
    assert 1500 in p.chainage_m  # original peak kept
    assert p.chainage_m[-1] == 4000
    assert p.elevation_m[p.chainage_m.index(1000)] == pytest.approx(200)
