import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pipeline_analytics.core.units import (
    Q_,
    UnitError,
    absolute_to_gauge,
    as_quantity,
    gauge_to_absolute,
    magnitude_in,
)


def test_mmscmd_to_m3_per_second() -> None:
    assert Q_(1, "MMSCMD").to("m**3/s").magnitude == pytest.approx(1e6 / 86400)


def test_mmscmd_to_mmscfd() -> None:
    # 1 m^3 = 35.3147 ft^3
    assert Q_(1, "MMSCMD").to("MMSCFD").magnitude == pytest.approx(35.3147, rel=1e-5)


def test_mmtpa_to_tonnes_per_hour() -> None:
    assert Q_(1, "MMTPA").to("t/h").magnitude == pytest.approx(1e6 / (365.25 * 24))


def test_bpd_to_m3_per_day() -> None:
    assert Q_(1000, "bpd").to("m**3/day").magnitude == pytest.approx(158.987, rel=1e-5)


def test_kgf_cm2_to_bar() -> None:
    assert Q_(1, "kgf_cm2").to("bar").magnitude == pytest.approx(0.980665)


def test_ksi_to_mpa_api5l_x70() -> None:
    # API 5L X70: SMYS 70 ksi ~ 483 MPa (485 MPa is the rounded SI value in the standard)
    assert Q_(70, "ksi").to("MPa").magnitude == pytest.approx(482.6, abs=0.1)


def test_inch_to_mm() -> None:
    assert Q_(24, "inch").to("mm").magnitude == pytest.approx(609.6)


def test_gauge_to_absolute_bar() -> None:
    assert gauge_to_absolute(Q_(92, "bar")).magnitude == pytest.approx(93.01325)


def test_gauge_to_absolute_keeps_unit() -> None:
    p = gauge_to_absolute(Q_(1000, "psi"))
    assert str(p.units) == "pound_force_per_square_inch"
    assert p.magnitude == pytest.approx(1014.696, abs=1e-3)


@given(st.floats(min_value=-1.0, max_value=500.0, allow_nan=False))
def test_gauge_absolute_round_trip(p_barg: float) -> None:
    back = absolute_to_gauge(gauge_to_absolute(Q_(p_barg, "bar")))
    assert math.isclose(back.magnitude, p_barg, abs_tol=1e-9)


def test_as_quantity_attaches_default_unit() -> None:
    assert as_quantity(5, "km") == Q_(5.0, "km")


def test_as_quantity_keeps_user_unit() -> None:
    q = as_quantity(Q_(3, "mile"), "km")
    assert str(q.units) == "mile"


def test_as_quantity_rejects_wrong_dimension() -> None:
    with pytest.raises(UnitError):
        as_quantity(Q_(3, "bar"), "km")


@pytest.mark.parametrize("bad", ["12", None, True])
def test_as_quantity_rejects_non_numbers(bad: object) -> None:
    with pytest.raises(UnitError):
        as_quantity(bad, "km")


def test_magnitude_in() -> None:
    assert magnitude_in(Q_(1, "km"), "m") == pytest.approx(1000.0)
    assert magnitude_in(250.0, "m") == pytest.approx(250.0)
