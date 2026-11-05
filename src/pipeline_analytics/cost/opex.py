"""Annual operating cost (OPEX)."""

from __future__ import annotations

from pydantic import Field

from pipeline_analytics.cost.rates import UnitRates, _Model


class OpexYear(_Model):
    """Inputs for one operating year."""

    operating_year: int = Field(ge=1)
    length_km: float = Field(gt=0)
    stations: int = Field(ge=0)
    electricity_kwh: float = Field(default=0.0, ge=0)
    fuel_gas_scm: float = Field(default=0.0, ge=0)
    station_capex: float = Field(default=0.0, ge=0, description="cumulative, for O&M %")
    cumulative_capex: float = Field(default=0.0, ge=0, description="for insurance")


class OpexBreakdown(_Model):
    energy: float
    line_om: float
    station_om: float
    manpower: float
    inspection: float
    insurance: float

    @property
    def total(self) -> float:
        return (
            self.energy
            + self.line_om
            + self.station_om
            + self.manpower
            + self.inspection
            + self.insurance
        )


def annual_opex(y: OpexYear, rates: UnitRates) -> OpexBreakdown:
    o = rates.opex
    ili = o.ili_per_km_run * y.length_km if y.operating_year % o.ili_interval_years == 0 else 0.0
    return OpexBreakdown(
        energy=y.electricity_kwh * o.electricity_per_kwh + y.fuel_gas_scm * o.fuel_gas_per_scm,
        line_om=o.line_om_per_km_year * y.length_km,
        station_om=o.station_om_fraction * y.station_capex,
        manpower=o.manpower_base_year + o.manpower_per_station_year * y.stations,
        inspection=ili,
        insurance=o.insurance_fraction * y.cumulative_capex,
    )
