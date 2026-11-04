import pytest
from pydantic import ValidationError

from pipeline_analytics.core.jurisdiction import (
    Jurisdiction,
    Service,
    StudyDefaults,
    code_basis,
)


def test_india_gas_code_basis() -> None:
    basis = code_basis(Service.GAS)
    assert basis.jurisdiction is Jurisdiction.INDIA
    assert "OISD-STD-226" in basis.design_codes
    assert "ASME B31.8" in basis.design_codes


def test_india_liquid_code_basis() -> None:
    basis = code_basis(Service.LIQUID)
    assert "OISD-STD-141" in basis.design_codes
    assert "ASME B31.4" in basis.design_codes


def test_study_defaults_india() -> None:
    d = StudyDefaults()
    assert d.jurisdiction is Jurisdiction.INDIA
    assert d.currency == "INR"
    assert d.gas_reference_temperature_c == 15.0


def test_discount_rate_bounds() -> None:
    with pytest.raises(ValidationError):
        StudyDefaults(discount_rate_real=0.9)
