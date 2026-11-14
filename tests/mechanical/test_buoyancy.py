import math

import pytest

from pipeline_analytics.mechanical import BuoyancyInput, PipeSection, buoyancy
from pipeline_analytics.mechanical.buoyancy import required_concrete_thickness

G = 9.80665


def test_empty_24in_floats_in_water(pipe24: PipeSection) -> None:
    r = buoyancy(pipe24, BuoyancyInput())
    d_c = 0.616
    assert r.buoyancy_n_per_m == pytest.approx(1000 * G * math.pi / 4 * d_c**2)
    coat = math.pi / 4 * (d_c**2 - 0.610**2) * 940 * G
    assert r.resisting_weight_n_per_m == pytest.approx(pipe24.weight_n_per_m + coat)
    assert not r.check.passed


def test_required_concrete_makes_it_pass(pipe24: PipeSection) -> None:
    r = buoyancy(pipe24, BuoyancyInput())
    t = r.required_concrete_thickness_m
    assert t == pytest.approx(0.0652, abs=5e-4)
    exact = buoyancy(pipe24, BuoyancyInput(concrete_thickness_m=t))
    assert exact.check.utilisation == pytest.approx(1.0)
    assert buoyancy(pipe24, BuoyancyInput(concrete_thickness_m=t + 0.005)).check.passed


def test_heavy_wall_small_pipe_sinks() -> None:
    p = PipeSection(od_m=0.1143, wt_m=0.0086, smys_pa=360e6)
    r = buoyancy(p, BuoyancyInput())
    assert r.check.passed
    assert r.required_concrete_thickness_m == 0.0


def test_liquefied_soil_needs_more_than_water(pipe24: PipeSection) -> None:
    water = buoyancy(pipe24, BuoyancyInput()).required_concrete_thickness_m
    liq = buoyancy(pipe24, BuoyancyInput(fluid_density_kg_m3=1900)).required_concrete_thickness_m
    assert liq > water


def test_concrete_too_light_returns_inf() -> None:
    b = BuoyancyInput(fluid_density_kg_m3=2200, safety_factor=1.2, concrete_density_kg_m3=2400)
    assert required_concrete_thickness(0.616, 1000.0, b) == math.inf


def test_contents_count_as_weight(pipe24: PipeSection) -> None:
    empty = buoyancy(pipe24, BuoyancyInput()).resisting_weight_n_per_m
    full = buoyancy(pipe24, BuoyancyInput(contents_density_kg_m3=850)).resisting_weight_n_per_m
    assert full - empty == pytest.approx(math.pi / 4 * pipe24.id_m**2 * 850 * G)
