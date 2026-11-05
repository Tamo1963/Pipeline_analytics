"""Jurisdiction and design-code basis for a study.

India is the first (and default) jurisdiction. The code basis lists which
documents a study is designed against; the rule modules that implement them
arrive in Phase 2 (``pipeline_analytics.codes``).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Jurisdiction(StrEnum):
    INDIA = "IN"
    BANGLADESH = "BD"


class Service(StrEnum):
    """Fluid service; selects the governing code family."""

    GAS = "gas"
    LIQUID = "liquid"


class CodeBasis(BaseModel):
    """Documents a design is checked against, in order of precedence."""

    model_config = ConfigDict(frozen=True)

    jurisdiction: Jurisdiction
    service: Service
    regulations: tuple[str, ...]
    design_codes: tuple[str, ...]


_CODE_BASIS: dict[tuple[Jurisdiction, Service], CodeBasis] = {
    (Jurisdiction.INDIA, Service.GAS): CodeBasis(
        jurisdiction=Jurisdiction.INDIA,
        service=Service.GAS,
        regulations=("PNGRB T4S Regulations for Natural Gas Pipelines",),
        design_codes=("OISD-STD-226", "ASME B31.8"),
    ),
    (Jurisdiction.INDIA, Service.LIQUID): CodeBasis(
        jurisdiction=Jurisdiction.INDIA,
        service=Service.LIQUID,
        regulations=("PNGRB T4S Regulations for Petroleum and Petroleum Products Pipelines",),
        design_codes=("OISD-STD-141", "ASME B31.4"),
    ),
    (Jurisdiction.BANGLADESH, Service.GAS): CodeBasis(
        jurisdiction=Jurisdiction.BANGLADESH,
        service=Service.GAS,
        regulations=(
            "To confirm: Bangladesh Energy Regulatory Commission (BERC) and national gas "
            "safety rules; GTCL / Petrobangla specifications",
        ),
        design_codes=("ASME B31.8",),
    ),
}


def code_basis(service: Service, jurisdiction: Jurisdiction = Jurisdiction.INDIA) -> CodeBasis:
    """Return the code basis for ``service`` in ``jurisdiction``."""
    try:
        return _CODE_BASIS[(jurisdiction, service)]
    except KeyError:  # pragma: no cover - only reachable once more jurisdictions exist
        raise ValueError(f"no code basis for {service} in {jurisdiction}") from None


class StudyDefaults(BaseModel):
    """Defaults applied when the user's brief does not specify a value.

    Every default used in a study is reported back as an assumption.
    """

    jurisdiction: Jurisdiction = Jurisdiction.INDIA
    currency: str = "INR"
    # Gas reference conditions per ISO 13443 (15 °C, 101.325 kPa). Confirm
    # against the gas sales/transport agreement for each project.
    gas_reference_temperature_c: float = 15.0
    gas_reference_pressure_kpa: float = 101.325
    discount_rate_real: float = Field(default=0.10, ge=0.0, le=0.5)
