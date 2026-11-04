import pytest

from pipeline_analytics.core.jurisdiction import Jurisdiction, Service
from pipeline_analytics.core.units import Q_
from pipeline_analytics.mechanical import LocationClass, PipeSection, pressure_design
from pipeline_analytics.mechanical.pressure import design_factor, temperature_derating


@pytest.mark.parametrize(
    ("lc", "f"),
    [
        (LocationClass.CLASS_1_DIV_2, 0.72),
        (LocationClass.CLASS_2, 0.60),
        (LocationClass.CLASS_3, 0.50),
        (LocationClass.CLASS_4, 0.40),
    ],
)
def test_gas_design_factors(lc: LocationClass, f: float) -> None:
    assert design_factor(Service.GAS, lc) == f


def test_class_1_div_1_not_applied_in_india_by_default() -> None:
    assert design_factor(Service.GAS, LocationClass.CLASS_1_DIV_1) == 0.72


def test_liquid_design_factor() -> None:
    assert design_factor(Service.LIQUID, LocationClass.CLASS_4) == 0.72


def test_temperature_derating() -> None:
    assert temperature_derating(50) == 1.0
    assert temperature_derating(121) == 1.0
    assert temperature_derating(149) == pytest.approx(0.967)
    assert temperature_derating(135) == pytest.approx((1.0 + 0.967) / 2)
    with pytest.raises(ValueError):
        temperature_derating(250)


def test_gas_hoop_and_required_wall(pipe24: PipeSection) -> None:
    r = pressure_design(pipe24, Q_(92, "bar"), Service.GAS)
    assert r.hoop_stress_pa == pytest.approx(92e5 * 0.610 / (2 * 0.00953))
    assert r.allowable_hoop_pa == pytest.approx(0.72 * 485e6)
    assert r.required_wt_m == pytest.approx(92e5 * 0.610 / (2 * 0.72 * 485e6))
    assert r.maop_pa == pytest.approx(2 * 0.72 * 485e6 * 0.00953 / 0.610)
    assert r.check.passed


def test_class_4_fails_same_pipe(pipe24: PipeSection) -> None:
    r = pressure_design(pipe24, 92e5, Service.GAS, LocationClass.CLASS_4)
    assert not r.check.passed
    assert r.required_wt_m == pytest.approx(92e5 * 0.610 / (2 * 0.40 * 485e6))


def test_allowance_added_and_excluded(pipe24: PipeSection) -> None:
    r = pressure_design(pipe24, 92e5, Service.LIQUID, allowance=0.001)
    assert r.required_wt_m == pytest.approx(92e5 * 0.610 / (2 * 0.72 * 485e6) + 0.001)
    assert r.hoop_stress_pa == pytest.approx(92e5 * 0.610 / (2 * 0.00853))


def test_maop_round_trip(pipe24: PipeSection) -> None:
    r = pressure_design(pipe24, 50e5, Service.GAS, LocationClass.CLASS_3)
    at_maop = pressure_design(pipe24, r.maop_pa, Service.GAS, LocationClass.CLASS_3)
    assert at_maop.check.utilisation == pytest.approx(1.0)


def test_invalid_inputs(pipe24: PipeSection) -> None:
    with pytest.raises(ValueError):
        pressure_design(pipe24, -1, Service.GAS)
    with pytest.raises(ValueError):
        pressure_design(pipe24, 1e6, Service.GAS, allowance=0.01)
    with pytest.raises(ValueError):
        pressure_design(pipe24, 1e6, Service.LIQUID, design_temp_c=130)


def test_other_jurisdiction_hook() -> None:
    # Only India exists today; the factor table is jurisdiction-aware.
    assert Jurisdiction.INDIA.value == "IN"
