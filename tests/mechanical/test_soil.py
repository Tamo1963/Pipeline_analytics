import math

import pytest
from pydantic import ValidationError

from pipeline_analytics.mechanical import Coating, Soil, SoilType, soil_springs
from pipeline_analytics.mechanical.soil import (
    adhesion_factor,
    axial_spring,
    bearing_factors,
    lateral_spring,
    n_ch,
    n_qh,
    uplift_spring,
)

D, H = 0.610, 1.505  # NPS 24, 1.2 m cover -> centreline depth


def test_axial_sand_hand_calc(dense_sand: Soil) -> None:
    k0 = 1 - math.sin(math.radians(36))
    expected = math.pi * D * H * 18e3 * (1 + k0) / 2 * math.tan(math.radians(0.6 * 36))
    s = axial_spring(D, H, dense_sand, Coating.POLYETHYLENE)
    assert s.max_force_n_per_m == pytest.approx(expected)
    assert s.max_force_n_per_m == pytest.approx(14514, rel=1e-3)
    assert s.yield_displacement_m == 0.003


def test_axial_coating_factor_orders_friction(dense_sand: Soil) -> None:
    concrete = axial_spring(D, H, dense_sand, Coating.CONCRETE).max_force_n_per_m
    fbe = axial_spring(D, H, dense_sand, Coating.FBE).max_force_n_per_m
    assert concrete > fbe


def test_axial_clay_hand_calc(soft_clay: Soil) -> None:
    alpha = adhesion_factor(25e3)
    assert 0.0 < alpha <= 1.0
    s = axial_spring(D, H, soft_clay, Coating.FBE)
    assert s.max_force_n_per_m == pytest.approx(math.pi * D * alpha * 25e3)
    assert s.yield_displacement_m == 0.010


def test_adhesion_factor_curve() -> None:
    # ALA fit: alpha = 0.608 - 0.123c - 0.274/(c^2+1) + 0.695/(c^3+1), c in ksf
    assert adhesion_factor(0.0) == pytest.approx(0.608 - 0.274 + 0.695)
    c = 47_880.26  # 1 ksf
    assert adhesion_factor(c) == pytest.approx(0.608 - 0.123 - 0.274 / 2 + 0.695 / 2, rel=1e-4)
    assert adhesion_factor(200e3) < adhesion_factor(50e3) < adhesion_factor(10e3)


def test_n_qh_table_values() -> None:
    # phi = 30, H/D = 3: 4.565 + 1.234*3 - 0.089*9 + 4.275e-3*27 - 9.159e-5*81
    assert n_qh(30, 3.0) == pytest.approx(7.574, abs=1e-3)
    assert n_qh(0, 3.0) == 0.0
    assert n_qh(32.5, 3.0) == pytest.approx((n_qh(30, 3.0) + n_qh(35, 3.0)) / 2)


def test_n_ch_capped_at_9() -> None:
    assert n_ch(50) == 9.0
    assert 0 < n_ch(1.0) < 9.0


def test_lateral_sand_hand_calc(dense_sand: Soil) -> None:
    s = lateral_spring(D, H, dense_sand)
    assert s.max_force_n_per_m == pytest.approx(n_qh(36, H / D) * 18e3 * H * D)
    assert s.max_force_n_per_m == pytest.approx(197_027, rel=1e-3)
    assert s.yield_displacement_m == pytest.approx(0.04 * (H + D / 2))


def test_lateral_displacement_cap(dense_sand: Soil) -> None:
    s = lateral_spring(D, 5.0, dense_sand)
    assert s.yield_displacement_m == pytest.approx(0.15 * D)


def test_uplift_sand_and_clay(dense_sand: Soil, soft_clay: Soil) -> None:
    s = uplift_spring(D, H, dense_sand)
    assert s.max_force_n_per_m == pytest.approx(36 * (H / D) / 44 * 18e3 * H * D)
    assert s.yield_displacement_m == pytest.approx(0.01 * H)
    c = uplift_spring(D, H, soft_clay)
    assert c.max_force_n_per_m == pytest.approx(2 * (H / D) * 25e3 * D)
    assert c.yield_displacement_m == pytest.approx(0.2 * D)


def test_bearing_factors() -> None:
    n_c, n_q, n_g = bearing_factors(30)
    assert n_q == pytest.approx(18.40, abs=0.01)  # classical Prandtl-Reissner value
    assert n_c == pytest.approx(30.14, abs=0.01)
    assert n_g == pytest.approx(math.exp(0.18 * 30 - 2.5))
    assert bearing_factors(0)[0] == pytest.approx(math.pi + 2, abs=1e-3)


def test_bearing_spring_clay(soft_clay: Soil) -> None:
    from pipeline_analytics.mechanical.soil import bearing_spring

    s = bearing_spring(D, H, soft_clay)
    # phi = 0: N_q = 1, N_gamma = exp(-2.5)
    expected = (math.pi + 2) * 25e3 * D + 17e3 * H * D + math.exp(-2.5) * 17e3 * D**2 / 2
    assert s.max_force_n_per_m == pytest.approx(expected, rel=1e-4)
    assert s.yield_displacement_m == pytest.approx(0.2 * D)


def test_springs_bundle(dense_sand: Soil) -> None:
    s = soil_springs(D, H, dense_sand, Coating.POLYETHYLENE)
    # bearing >> lateral >> uplift > axial is the usual order in dense sand
    assert (
        s.bearing.max_force_n_per_m
        > s.lateral.max_force_n_per_m
        > s.uplift.max_force_n_per_m
        > s.axial.max_force_n_per_m
    )
    assert s.axial.stiffness_n_per_m2 == pytest.approx(s.axial.max_force_n_per_m / 0.003)


def test_submerged_soil_reduces_resistance() -> None:
    dry = Soil(soil_type=SoilType.LOOSE_SAND, unit_weight_n_m3=19e3, friction_angle_deg=30)
    wet = Soil(
        soil_type=SoilType.LOOSE_SAND,
        unit_weight_n_m3=19e3,
        friction_angle_deg=30,
        below_water_table=True,
    )
    assert wet.effective_unit_weight_n_m3 == pytest.approx(19e3 - 9806.65)
    assert (
        axial_spring(D, H, wet, Coating.FBE).max_force_n_per_m
        < axial_spring(D, H, dry, Coating.FBE).max_force_n_per_m
    )


def test_soil_validation() -> None:
    with pytest.raises(ValidationError):
        Soil(soil_type=SoilType.DENSE_SAND, unit_weight_n_m3=18e3)
    with pytest.raises(ValidationError):
        Soil(
            soil_type=SoilType.DENSE_SAND,
            unit_weight_n_m3=9e3,
            friction_angle_deg=30,
            below_water_table=True,
        )


def test_too_shallow_rejected(dense_sand: Soil) -> None:
    with pytest.raises(ValueError):
        axial_spring(D, 0.2, dense_sand, Coating.FBE)


def test_soil_of_quantities() -> None:
    from pipeline_analytics.core.units import Q_

    s = Soil.of(SoilType.STIFF_CLAY, Q_(120, "lbf/ft**3"), cohesion=Q_(1, "kip/ft**2"))
    assert s.unit_weight_n_m3 == pytest.approx(18_850, rel=1e-3)
    assert s.cohesion_pa == pytest.approx(47_880, rel=1e-3)
