"""Liquefaction triggering (Idriss & Boulanger 2008), lateral spread (Youd et al. 2002),
and the pipe checks built on them."""

import itertools
import math

import pytest

from pipeline_analytics.geotech import (
    Earthquake,
    SptLayer,
    SptProfile,
    deltaic_profile,
    lateral_spread,
    triggering,
    youd2002,
)
from pipeline_analytics.geotech.liquefaction import crr_75, delta_n_fines, k_sigma, msf, rd
from pipeline_analytics.geotech.pipe import bank_spread_check, flotation_check
from pipeline_analytics.mechanical import pipe_from_library
from pipeline_analytics.mechanical.pgd import GroundMovement, lateral_spread_movement
from pipeline_analytics.mechanical.soil import Coating, Soil, SoilType, soil_springs

LOCAL = Earthquake(magnitude=7.5, pga_g=0.18, distance_km=30)


def test_component_relations() -> None:
    assert msf(7.5) == pytest.approx(1.0, abs=0.002)
    assert msf(5.0) == pytest.approx(1.8)  # capped
    assert crr_75(30.0) == pytest.approx(0.485, abs=0.005)
    assert delta_n_fines(2.0) == pytest.approx(0.0, abs=1e-3)  # clean sand
    assert delta_n_fines(35.0) == pytest.approx(5.5, abs=0.1)  # plateau
    assert k_sigma(20.0, 10.0) == pytest.approx(1.1)  # capped at low stress
    assert k_sigma(400.0, 10.0) < 1.0
    assert rd(0.0, 7.5) == pytest.approx(1.0, abs=0.01)


def test_layer_hand_calculation() -> None:
    """Loose silty sand at 3.5 m (independent arithmetic of the I&B 2008 equations)."""
    r = triggering(deltaic_profile(), LOCAL).layers[1]
    sv = 2 * 17.5 + 1.5 * 18.0
    se = sv - 9.81 * 2.5
    assert (r.sigma_v_kpa, r.sigma_v_eff_kpa) == pytest.approx((sv, se))
    assert r.csr == pytest.approx(0.65 * 0.18 * sv / se * rd(3.5, 7.5))
    m = 0.784 - 0.0768 * math.sqrt(r.n1_60cs)
    assert r.n1_60 == pytest.approx(6 * (101.325 / se) ** m, rel=1e-6)
    assert r.n1_60cs == pytest.approx(r.n1_60 + delta_n_fines(30))
    assert r.crr == pytest.approx(crr_75(r.n1_60cs) * 1.1 * msf(7.5))  # K_sigma capped
    assert r.factor_of_safety == pytest.approx(0.91, abs=0.01) and r.liquefies


def test_susceptibility_and_lpi() -> None:
    t = triggering(deltaic_profile(), LOCAL)
    assert not t.layers[0].susceptible  # clay crust (PI 15)
    assert t.lpi > 5 and t.liquefied_thickness_m > 0
    dry = SptProfile(layers=(SptLayer(top_m=0, bottom_m=10, n60=5, fines_pct=10, d50_mm=0.2),),
                     water_table_m=20)  # fmt: skip
    td = triggering(dry, LOCAL)
    assert td.lpi == 0 and not td.layers[0].susceptible
    weak = triggering(deltaic_profile(), Earthquake(magnitude=8, pga_g=0.08, distance_km=200))
    assert weak.lpi == 0 and weak.youd_inputs() == (0.0, 0.0, 0.0)
    with pytest.raises(ValueError, match="contiguous"):
        SptProfile(layers=(SptLayer(top_m=0, bottom_m=2, n60=5),
                           SptLayer(top_m=3, bottom_m=4, n60=5)), water_table_m=1)  # fmt: skip


def test_youd2002_equation() -> None:
    m, r, t, f, d, w = 7.5, 30.0, 7.0, 21.4, 0.13, 20.0
    r_star = r + 10 ** (0.89 * m - 5.64)
    expected = 10 ** (-16.713 + 1.532 * m - 1.406 * math.log10(r_star) - 0.012 * r
                      + 0.592 * math.log10(w) + 0.540 * math.log10(t)
                      + 3.413 * math.log10(100 - f) - 0.795 * math.log10(d + 0.1))  # fmt: skip
    assert youd2002(m, r, t, f, d, free_face_ratio_pct=w) == pytest.approx(expected)
    ff, sl = youd2002(m, r, t, f, d, free_face_ratio_pct=4), youd2002(m, r, t, f, d, slope_pct=4)
    assert sl / ff == pytest.approx(10 ** (0.5 + (0.338 - 0.592) * math.log10(4)))
    assert youd2002(m, 2 * r, t, f, d, free_face_ratio_pct=w) < expected
    with pytest.raises(ValueError):
        youd2002(m, r, t, f, d)


def test_lateral_spread_profile() -> None:
    ls = lateral_spread(triggering(deltaic_profile(), LOCAL), free_face_height_m=5.0)
    disp = [p.displacement_m for p in ls.profile]
    assert all(a >= b for a, b in itertools.pairwise(disp))  # decays inland
    assert ls.max_displacement_m == pytest.approx(disp[0]) and ls.extent_m > 100
    # W^0.592 decay between two distances
    a, b = ls.profile[3], ls.profile[10]
    assert b.displacement_m / a.displacement_m == pytest.approx(
        (a.distance_m / b.distance_m) ** 0.592, rel=1e-6
    )
    none = lateral_spread(triggering(deltaic_profile(), Earthquake(
        magnitude=8, pga_g=0.08, distance_km=200)), free_face_height_m=5.0)  # fmt: skip
    assert none.mode == "none" and none.max_displacement_m == 0


def test_movement_profile_and_setback() -> None:
    ls = lateral_spread(triggering(deltaic_profile(), LOCAL), free_face_height_m=5.0)
    mv = lateral_spread_movement(ls, 0.0, hdd_setback_m=100.0, transition_m=30.0)
    assert mv.axial_m == pytest.approx(-ls.displacement_at(100.0))  # towards the river
    g = mv.field()
    import numpy as np

    vals = g(np.array([-50.0, -15.0, 0.0, 200.0]))[0]
    assert vals[0] == 0 and vals[1] == pytest.approx(mv.axial_m / 2) and vals[2] == mv.axial_m
    with pytest.raises(ValueError):
        GroundMovement(kind="table", profile_x_m=(0, 0), profile_scale=(1, 0))


@pytest.fixture(scope="module")
def soft_clay():  # type: ignore[no-untyped-def]
    return Soil(soil_type=SoilType.SOFT_CLAY, unit_weight_n_m3=17.5e3, cohesion_pa=15e3,
                below_water_table=True)  # fmt: skip


def test_bank_spread_setback(soft_clay) -> None:  # type: ignore[no-untyped-def]
    ls = lateral_spread(triggering(deltaic_profile(), LOCAL), free_face_height_m=5.0)
    pipe = pipe_from_library("24", 6.5, "X65")
    sp = soil_springs(pipe.od_m, 1.5 + pipe.od_m / 2, soft_clay, Coating.POLYETHYLENE)
    c = bank_spread_check(pipe, sp, ls, grade="X65", pressure_pa=69e5,
                          setbacks_m=(0, 50, 200, 2000))  # fmt: skip
    ur = [x.utilisation for x in c.cases]
    assert ur[0] > 1 and ur[0] > ur[1] > ur[2]
    assert c.required_setback_m == 50 and c.cases[-1].notes == ("beyond the spreading ground",)
    soft = bank_spread_check(pipe, sp, ls, grade="X65", pressure_pa=69e5, setbacks_m=(0,),
                             moving_ground_spring_factor=0.1)  # fmt: skip
    assert soft.cases[0].utilisation < ur[0]  # liquefied springs: less load on the pipe


def test_flotation() -> None:
    pipe = pipe_from_library("24", 6.5, "X65")
    f = flotation_check(pipe)
    assert f.floats and f.net_uplift_n_per_m > 0 and f.required_concrete_mm > 100
    held = flotation_check(pipe, concrete_thickness_m=f.required_concrete_mm / 1000 + 0.001)
    assert not held.floats
