"""Study state held by the Feasibility Bot across a conversation.

The bot never computes numbers itself. It fills a :class:`Study` through tools
(route, design basis) and asks the engine for results. Every default the study
applies is recorded in ``assumptions`` so the bot can state it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from pipeline_analytics.core.jurisdiction import Jurisdiction, Service
from pipeline_analytics.cost.rates import UnitRates, placeholder_rates
from pipeline_analytics.economics.cashflow import Concession
from pipeline_analytics.economics.option import OptionInput, OptionResult, ramp_profile
from pipeline_analytics.hydraulics import Gas, GasStationInput, Liquid, Profile, PumpStationInput
from pipeline_analytics.mechanical import SegmentInput, Soil, SoilType
from pipeline_analytics.optimiser import Alignment, ScenarioTree

MMSCF_TO_MMSCM = 0.0283168
PSI_TO_PA = 6894.757
INR_PER_USD_DEFAULT = 88.0


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DesignBasis(_In):
    """Everything about the project except the route."""

    service: Literal["gas", "liquid"]
    jurisdiction: Literal["IN", "BD"] = "IN"
    throughput_start: float = Field(gt=0, description="year-1 flow: MMSCFD (gas) or MMTPA (liquid)")
    throughput_plateau: float | None = Field(
        default=None, gt=0, description="plateau flow, same unit"
    )
    ramp_years: int = Field(default=1, ge=1)
    operating_years: int = Field(default=25, ge=1, le=50, description="concession operating period")
    construction_years: int = Field(default=2, ge=1, le=6)
    discount_rate: float = Field(default=0.10, ge=0, lt=0.5)
    design_pressures_barg: list[float] = Field(
        default_factory=lambda: [68.95, 99.3], description="MAOP options to consider"
    )
    supply_pressure_barg: float = Field(default=60.0, description="inlet pressure, year 1")
    supply_pressure_end_barg: float | None = Field(
        default=None,
        description="inlet pressure in the last year (linear decline); None = constant",
    )
    delivery_min_barg: float = Field(default=30.0)
    gas_specific_gravity: float = Field(default=0.6, gt=0.5, lt=1.0)
    liquid_density_kg_m3: float = Field(default=850.0, gt=300, lt=1300)
    liquid_viscosity_cst: float = Field(default=10.0, gt=0)
    compressor_driver: Literal["gas_turbine", "electric"] = "gas_turbine"
    soft_ground_burial_checks: bool = Field(
        default=False, description="run ALA buried-pipe checks for soft, waterlogged soil"
    )
    inr_per_usd: float = Field(default=INR_PER_USD_DEFAULT, gt=0)


@dataclass
class Study:
    """One feasibility study in a conversation."""

    basis: DesignBasis | None = None
    alignment: Alignment | None = None
    rates: UnitRates = field(default_factory=placeholder_rates)
    tree: ScenarioTree = field(default_factory=ScenarioTree)
    assumptions: dict[str, str] = field(default_factory=dict)
    last_best: dict[str, Any] | None = None
    last_search: Any = None
    """The last optimiser DesignSearch (feeds generate_report)."""
    sensitivity_rows: list[Any] = field(default_factory=list)
    """SensitivityRow objects from the last run_sensitivity call."""
    risk_rows: list[Any] = field(default_factory=list)
    """RiskRow objects from the last run_risk_analysis call."""
    risk_note: str = ""
    report_dir: Path = field(default_factory=lambda: Path("reports_out"))
    last_report_files: dict[str, str] = field(default_factory=dict)

    # ------------------------------------------------------------------ build
    def require(self) -> tuple[DesignBasis, Alignment]:
        if self.basis is None:
            raise ValueError("no design basis yet: call set_design_basis first")
        if self.alignment is None:
            raise ValueError("no route yet: call define_route first")
        return self.basis, self.alignment

    def throughput(
        self, basis: DesignBasis | None = None, factor: float = 1.0
    ) -> tuple[float, ...]:
        b = basis or self.basis
        assert b is not None
        plateau = b.throughput_plateau or b.throughput_start
        prof = ramp_profile(b.throughput_start, plateau, min(b.ramp_years, b.operating_years),
                            b.operating_years)  # fmt: skip
        conv = MMSCF_TO_MMSCM if b.service == "gas" else 1.0
        return tuple(q * conv * factor for q in prof)

    def option_input(
        self,
        nps: str = "24",
        grade: str = "X70",
        design_pressure_barg: float | None = None,
        basis: DesignBasis | None = None,
        alignment: Alignment | None = None,
        rates: UnitRates | None = None,
        throughput_factor: float = 1.0,
    ) -> OptionInput:
        b = basis or self.basis
        al = alignment or self.alignment
        if b is None or al is None:
            self.require()
        assert b is not None and al is not None
        service = Service.GAS if b.service == "gas" else Service.LIQUID
        p_design = (design_pressure_barg or b.design_pressures_barg[0]) * 1e5
        years = b.operating_years
        supply: tuple[float, ...] | None = None
        if b.supply_pressure_end_barg is not None:
            s0, s1 = b.supply_pressure_barg, b.supply_pressure_end_barg
            supply = tuple((s0 + (s1 - s0) * t / max(years - 1, 1)) * 1e5 for t in range(years))
        construction = b.construction_years
        data: dict[str, Any] = {
            "service": service,
            "nps": nps,
            "grade": grade,
            "design_pressure_pa_g": p_design,
            "profile": Profile.flat(1000),
            "throughput": self.throughput(b, throughput_factor),
            "gas": Gas(specific_gravity=b.gas_specific_gravity),
            "liquid": Liquid(
                density_kg_m3=b.liquid_density_kg_m3,
                kinematic_viscosity_m2_s=b.liquid_viscosity_cst * 1e-6,
            ),
            "gas_stations": GasStationInput(
                supply_pressure_pa_g=b.supply_pressure_barg * 1e5,
                delivery_min_pa_g=b.delivery_min_barg * 1e5,
                min_suction_pa_g=min(25e5, b.supply_pressure_barg * 1e5),
            ),
            "pump_stations": PumpStationInput(
                supply_pressure_pa_g=b.supply_pressure_barg * 1e5,
                delivery_min_pa_g=b.delivery_min_barg * 1e5,
            ),
            "supply_pressure_by_year": supply,
            "compressor_driver": b.compressor_driver,
            "concession": Concession(
                construction_years=construction,
                capex_phasing=tuple(1 / construction for _ in range(construction)),
                operating_years=years,
                discount_rate=b.discount_rate,
            ),
            "rates": rates or self.rates,
        }
        if b.soft_ground_burial_checks:
            data["burial"] = SegmentInput(
                service=service,
                design_pressure_pa=p_design,
                cover_m=1.5,
                soil=Soil(
                    soil_type=SoilType.SOFT_CLAY,
                    unit_weight_n_m3=17.5e3,
                    cohesion_pa=15e3,
                    below_water_table=True,
                ),
                modulus_of_soil_reaction_pa=1.4e6,
                water_above_crown_m=1.5,
            )
        return al.option_input(OptionInput.model_validate(data))

    def jurisdiction(self) -> Jurisdiction:
        return Jurisdiction(self.basis.jurisdiction) if self.basis else Jurisdiction.INDIA

    # --------------------------------------------------------------- reports
    def usd_m(self, inr: float) -> float:
        rate = self.basis.inr_per_usd if self.basis else INR_PER_USD_DEFAULT
        return round(inr / rate / 1e6, 2)

    def per_mmbtu_or_tonne(self, res: OptionResult, inr_per_unit: float) -> float:
        rate = self.basis.inr_per_usd if self.basis else INR_PER_USD_DEFAULT
        if res.throughput_unit == "scm":
            return round(inr_per_unit * res.scm_per_mmbtu / rate, 3)
        return round(inr_per_unit / rate, 3)

    def summary(
        self, res: OptionResult, inp: OptionInput, target_return: float = 0.12
    ) -> dict[str, Any]:
        if not res.feasible or res.cashflow is None or res.capex is None:
            return {"nps": res.nps, "feasible": False, "reason": "; ".join(res.issues)}
        tariff = res.cashflow.tariff_for_return(target_return)
        unit = "USD_per_MMBtu" if res.throughput_unit == "scm" else "USD_per_tonne"
        return {
            "nps": res.nps,
            "grade": res.grade,
            "maop_barg": round(inp.design_pressure_pa_g / 1e5, 2),
            "feasible": True,
            "walls_mm_by_location_class": {
                w.location_class.value: round(w.wt_m * 1000, 1) for w in res.walls
            },
            "wall_governed_by": sorted({w.governing for w in res.walls}),
            "pipe_tonnes": round(res.pipe_tonnes),
            "capex_usd_m": self.usd_m(res.capex.total),
            "pv_capex_usd_m": self.usd_m(res.pv_capex),
            "pv_opex_usd_m": self.usd_m(res.pv_opex),
            "lifecycle_cost_pv_usd_m": self.usd_m(res.lcc_pv),
            "max_stations": max(h.stations for h in res.hydraulics),
            "first_station_year": next(
                (h.operating_year for h in res.hydraulics if h.stations), None
            ),
            "peak_station_power_mw": round(max(h.peak_power_kw for h in res.hydraulics) / 1000, 2),
            f"levelised_cost_{unit}": self.per_mmbtu_or_tonne(res, res.levelised_cost),
            f"tariff_{int(target_return * 100)}pct_post_tax_{unit}": self.per_mmbtu_or_tonne(
                res, tariff
            ),
        }

    def disclosures(self) -> list[str]:
        out = []
        if self.rates.placeholder:
            out.append(
                "Costs use PLACEHOLDER India 2026 unit rates (AACE Class 5 screening); "
                "USD at an assumed exchange rate. Compare options; do not quote as a budget."
            )
        out.extend(f"{k}: {v}" for k, v in self.assumptions.items())
        return out


def finite(x: float) -> float | None:
    return x if math.isfinite(x) else None
