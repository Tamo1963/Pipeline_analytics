import math

import pytest

from pipeline_analytics.mechanical import PipeSection, VerticalLoadInput, vertical_loads
from pipeline_analytics.mechanical.vertical_loads import boussinesq_pressure_pa, earth_load_n_per_m


def test_earth_load() -> None:
    assert earth_load_n_per_m(0.610, 1.2, 18e3) == pytest.approx(18e3 * 1.2 * 0.610)
    with pytest.raises(ValueError):
        earth_load_n_per_m(0.6, -1, 18e3)


def test_boussinesq_directly_above() -> None:
    p = boussinesq_pressure_pa(71e3, 1.2, impact_factor=1.0)
    assert p == pytest.approx(3 * 71e3 / (2 * math.pi * 1.2**2))
    assert boussinesq_pressure_pa(71e3, 1.2, offset=1.0) < boussinesq_pressure_pa(71e3, 1.2)
    with pytest.raises(ValueError):
        boussinesq_pressure_pa(1.0, 0.0)


def test_iowa_and_buckling_hand_calc(pipe24: PipeSection) -> None:
    burial = VerticalLoadInput(
        cover_m=1.2, soil_unit_weight_n_m3=18e3, modulus_of_soil_reaction_pa=6.9e6
    )
    r = vertical_loads(pipe24, burial)
    w = 18e3 * 1.2 * 0.610
    rm = (0.610 - 0.00953) / 2
    ei = 207e9 * 0.00953**3 / 12
    dy = 1.5 * 0.1 * w * rm**3 / (ei + 0.061 * 6.9e6 * rm**3)
    assert r.deflection_m == pytest.approx(dy)
    assert r.deflection_ratio == pytest.approx(0.00333, rel=1e-2)
    # H/D = 1.97 < 2 -> FS 3.0
    b = 1 / (1 + 4 * math.exp(-0.065 * 1.2 / 0.3048))
    q_a = math.sqrt(32 * 1.0 * b * 6.9e6 * ei / 0.610**3) / 3.0
    assert r.buckling_pressure_allowable_pa == pytest.approx(q_a, rel=1e-3)
    assert r.buckling_pressure_applied_pa == pytest.approx(w / 0.610)
    assert all(c.passed for c in r.checks)


def test_thin_wall_shallow_cover_heavy_traffic_fails() -> None:
    thin = PipeSection(od_m=1.219, wt_m=0.0079, smys_pa=485e6)
    burial = VerticalLoadInput(
        cover_m=0.6,
        soil_unit_weight_n_m3=20e3,
        modulus_of_soil_reaction_pa=0.7e6,
        live_load_pressure_pa=boussinesq_pressure_pa(100e3, 0.6),
    )
    r = vertical_loads(thin, burial)
    assert not all(c.passed for c in r.checks)


def test_water_table_reduces_buckling_capacity(pipe24: PipeSection) -> None:
    dry = VerticalLoadInput(
        cover_m=2.0, soil_unit_weight_n_m3=18e3, modulus_of_soil_reaction_pa=7e6
    )
    wet = dry.model_copy(update={"water_above_crown_m": 2.0})
    assert (
        vertical_loads(pipe24, wet).buckling_pressure_allowable_pa
        < vertical_loads(pipe24, dry).buckling_pressure_allowable_pa
    )
