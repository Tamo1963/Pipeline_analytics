"""Unit-rate sets for the cost engine.

A rate set is a YAML file (see ``data/placeholder_in_2026.yaml``). The bundled
set is a clearly labelled placeholder; load your own with :func:`load_rates`.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from functools import cache
from importlib import resources
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field


class _Model(BaseModel):
    # ser_json_inf_nan: inf (e.g. "no limit") survives a JSON round trip
    model_config = ConfigDict(frozen=True, extra="forbid", ser_json_inf_nan="constants")


class Terrain(StrEnum):
    FLAT = "flat"
    ROLLING = "rolling"
    HILLY = "hilly"
    ROCK = "rock"
    MARSH = "marsh"
    URBAN = "urban"
    FOREST = "forest"


class LandUse(StrEnum):
    AGRICULTURAL = "agricultural"
    FOREST = "forest"
    URBAN = "urban"
    WASTELAND = "wasteland"


class CrossingType(StrEnum):
    NATIONAL_HIGHWAY = "national_highway"
    STATE_HIGHWAY = "state_highway"
    MINOR_ROAD = "minor_road"
    RAILWAY = "railway"
    CANAL = "canal"
    FOREIGN_PIPELINE = "foreign_pipeline"


class LinePipeRates(_Model):
    base_rate_per_tonne: float = Field(gt=0)
    grade_factor: dict[str, float]
    process_factor: dict[str, float]


class CrossingRates(_Model):
    hdd_per_inch_m: float = Field(ge=0)
    open_cut_river_per_inch_m: float = Field(ge=0)
    per_crossing: dict[CrossingType, float]


class StationRates(_Model):
    compressor_fixed: float = Field(ge=0)
    compressor_per_kw: float = Field(ge=0)
    pump_fixed: float = Field(ge=0)
    pump_per_kw: float = Field(ge=0)
    installed_power_factor: float = Field(ge=1)
    terminal_each: float = Field(ge=0)
    sectionalising_valve_each: float = Field(ge=0)
    scraper_station_each: float = Field(ge=0)


class PerKmRates(_Model):
    cathodic_protection: float = Field(ge=0)
    scada_telecom: float = Field(ge=0)


class LandRates(_Model):
    row_width_m: float = Field(gt=0)
    rou_compensation_per_ha: dict[LandUse, float]
    station_land_ha: float = Field(ge=0)
    permanent_land_per_ha: float = Field(ge=0)


class Indirects(_Model):
    taxes: float = Field(ge=0, lt=1)
    engineering_pmc: float = Field(ge=0, lt=1)
    owners_cost: float = Field(ge=0, lt=1)
    contingency: float = Field(ge=0, lt=1)


class OpexRates(_Model):
    electricity_per_kwh: float = Field(ge=0)
    fuel_gas_per_scm: float = Field(ge=0)
    line_om_per_km_year: float = Field(ge=0)
    station_om_fraction: float = Field(ge=0, lt=1)
    manpower_per_station_year: float = Field(ge=0)
    manpower_base_year: float = Field(ge=0)
    ili_per_km_run: float = Field(ge=0)
    ili_interval_years: int = Field(ge=1)
    insurance_fraction: float = Field(ge=0, lt=1)


class RateMeta(_Model):
    """Who owns a rate set and whether it is approved for use (all optional)."""

    name: str = ""
    region: str = ""
    approved_by: str = ""
    approved_on: date | None = None
    valid_until: date | None = None
    notes: str = ""
    escalated_from: str = ""
    """Set by ``rate_library.escalate``: the source set and the indices applied."""


class UnitRates(_Model):
    currency: str
    base_year: int
    placeholder: bool
    source: str
    meta: RateMeta = Field(default_factory=RateMeta)
    line_pipe: LinePipeRates
    coating_per_m2: dict[str, float]
    concrete_coating_per_m3: float = Field(ge=0)
    laying_per_inch_km: float = Field(gt=0)
    terrain_factor: dict[Terrain, float]
    crossings: CrossingRates
    stations: StationRates
    per_km: PerKmRates
    land: LandRates
    indirects: Indirects
    opex: OpexRates

    def pipe_rate_per_tonne(self, grade: str, process: str) -> float:
        lp = self.line_pipe
        try:
            return lp.base_rate_per_tonne * lp.grade_factor[grade] * lp.process_factor[process]
        except KeyError as e:
            raise KeyError(f"no line-pipe rate factor for {e.args[0]!r}") from None


def load_rates(path: str | Path) -> UnitRates:
    """Load a unit-rate set from a YAML file."""
    return UnitRates(**yaml.safe_load(Path(path).read_text()))


@cache
def placeholder_rates() -> UnitRates:
    """The bundled PLACEHOLDER India 2026 rate set (screening and testing only)."""
    text = resources.files("pipeline_analytics.cost").joinpath("data", "placeholder_in_2026.yaml")
    return UnitRates(**yaml.safe_load(text.read_text()))


CRORE = 1e7


def crore(amount_inr: float) -> float:
    """INR to crore (1 crore = 10 million)."""
    return amount_inr / CRORE


# ---------------------------------------------------------------- rate groups
RATE_GROUPS: dict[str, str] = {
    "line_pipe": "Line pipe price per tonne",
    "construction": "Laying, crossings (HDD, open cut, roads, rail) and concrete coating",
    "stations": "Compressor / pump stations, terminals, valve and scraper stations",
    "other_capex": "External coating, CP, SCADA / telecom, right of use and station land",
    "energy": "Electricity and fuel-gas prices",
    "o_and_m": "Line O&M, manpower and in-line inspection rates",
}
"""Groups used by sensitivity and risk analysis.

Percentage-based items (station O&M % of station CAPEX, insurance % of CAPEX,
indirect cost %) are deliberately in no group: they already scale with the
CAPEX groups, and scaling them too would create cross-terms. With these groups
a fixed design's lifecycle cost is exactly linear in each group's factor.
"""


def scale_rate_groups(rates: UnitRates, factors: dict[str, float]) -> UnitRates:
    """Copy of ``rates`` with each named group multiplied by its factor (validated rebuild)."""
    unknown = set(factors) - set(RATE_GROUPS)
    if unknown:
        raise KeyError(f"unknown rate groups: {sorted(unknown)}")
    d = rates.model_dump()
    k = factors.get("line_pipe", 1.0)
    d["line_pipe"]["base_rate_per_tonne"] *= k
    k = factors.get("construction", 1.0)
    d["laying_per_inch_km"] *= k
    d["concrete_coating_per_m3"] *= k
    cr = d["crossings"]
    cr["hdd_per_inch_m"] *= k
    cr["open_cut_river_per_inch_m"] *= k
    cr["per_crossing"] = {c: v * k for c, v in cr["per_crossing"].items()}
    k = factors.get("stations", 1.0)
    for f in ("compressor_fixed", "compressor_per_kw", "pump_fixed", "pump_per_kw",
              "terminal_each", "sectionalising_valve_each", "scraper_station_each"):  # fmt: skip
        d["stations"][f] *= k
    k = factors.get("other_capex", 1.0)
    d["coating_per_m2"] = {c: v * k for c, v in d["coating_per_m2"].items()}
    d["per_km"] = {c: v * k for c, v in d["per_km"].items()}
    d["land"]["rou_compensation_per_ha"] = {
        c: v * k for c, v in d["land"]["rou_compensation_per_ha"].items()
    }
    d["land"]["permanent_land_per_ha"] *= k
    k = factors.get("energy", 1.0)
    d["opex"]["electricity_per_kwh"] *= k
    d["opex"]["fuel_gas_per_scm"] *= k
    k = factors.get("o_and_m", 1.0)
    for f in ("line_om_per_km_year", "manpower_per_station_year", "manpower_base_year",
              "ili_per_km_run"):  # fmt: skip
        d["opex"][f] *= k
    return UnitRates.model_validate(d)


CAPITAL_GROUPS = ("line_pipe", "construction", "stations", "other_capex")


def scale_capital(rates: UnitRates, k: float) -> UnitRates:
    """All capital rate groups x ``k`` (OPEX rates unchanged)."""
    return scale_rate_groups(rates, dict.fromkeys(CAPITAL_GROUPS, k))
