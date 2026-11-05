"""Evaluate one pipeline option end to end, and the economic-diameter curve.

An *option* is a diameter + grade on a given route, throughput profile and
concession. Steps:

1. Wall thickness per location class: pressure design (B31.4 / B31.8 with
   OISD / T4S factors) and a D/t limit, rounded up to the mill increment.
   With a ``burial`` template, the ALA buried-pipe checks also run and the wall
   is increased until every check except buoyancy passes. Buoyancy is handled
   by concrete coating over ``wet_length_m``.
2. Cheapest manufacturing process that can make the OD and wall (PipeSpec
   ranges x rate factors).
3. Hydraulics for every operating year: peak flow (average / load factor)
   sizes the stations; average flow gives the energy. Stations are phased:
   added in the year before they are first needed.
4. CAPEX (BOQ), OPEX per year, concession cash flow, and lifecycle cost.

Throughput units: gas in MMSCMD (annual average), liquids in MMTPA.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Literal

from pydantic import Field

from pipeline_analytics.codes.class_location import ClassSegment
from pipeline_analytics.core.jurisdiction import Service
from pipeline_analytics.core.units import Q_
from pipeline_analytics.cost.boq import (
    CapexEstimate,
    PipeSegmentQty,
    RouteQuantities,
    StationQty,
    capex_estimate,
    station_items,
    with_indirects,
)
from pipeline_analytics.cost.opex import OpexBreakdown, OpexYear, annual_opex
from pipeline_analytics.cost.rates import UnitRates, _Model, placeholder_rates
from pipeline_analytics.economics.cashflow import CashFlow, Concession, build_cashflow
from pipeline_analytics.hydraulics import (
    Gas,
    GasStationInput,
    HydraulicsError,
    Liquid,
    Offtake,
    Profile,
    PumpStationInput,
    gas_hydraulics,
    liquid_hydraulics,
)
from pipeline_analytics.mechanical.assessment import SegmentInput, assess_segment
from pipeline_analytics.mechanical.buoyancy import BuoyancyInput, buoyancy
from pipeline_analytics.mechanical.common import PipeSection
from pipeline_analytics.mechanical.pressure import LocationClass, pressure_design
from pipeline_analytics.pipespec import default_library

KCAL_PER_MMBTU = 252_000.0
HOURS_PER_YEAR = 8760.0
DAYS_PER_YEAR = 365.0


def ramp_profile(
    start: float, plateau: float, ramp_years: int, operating_years: int
) -> tuple[float, ...]:
    """Linear ramp from ``start`` in year 1 to ``plateau`` in year ``ramp_years``, then flat."""
    if ramp_years < 1 or ramp_years > operating_years:
        raise ValueError("ramp_years must be between 1 and operating_years")
    out = []
    for t in range(1, operating_years + 1):
        frac = (t - 1) / (ramp_years - 1) if ramp_years > 1 else 1.0
        out.append(start + (plateau - start) * min(frac, 1.0))
    return tuple(out)


class OptionInput(_Model):
    service: Service
    nps: str | float
    grade: str = "X70"
    psl: int = 2
    design_pressure_pa_g: float = Field(gt=0)
    profile: Profile
    class_segments: tuple[ClassSegment, ...] = ()
    """Gas only; empty = Class 1 throughout."""
    route: RouteQuantities = RouteQuantities()
    throughput: tuple[float, ...]
    """Annual average flow per operating year: MMSCMD (gas) or MMTPA (liquid)."""
    load_factor: float = Field(default=0.9, gt=0, le=1, description="average / peak flow")
    gas: Gas = Gas()
    liquid: Liquid = Liquid()
    gas_stations: GasStationInput | None = None
    pump_stations: PumpStationInput | None = None
    offtakes: tuple[Offtake, ...] = ()
    """Intermediate deliveries (share of inlet flow, minimum pressure)."""
    supply_pressure_by_year: tuple[float, ...] | None = None
    """Inlet supply pressure (Pa gauge) per operating year, e.g. declining field pressure.
    Overrides the station input's supply pressure."""
    compressor_driver: Literal["gas_turbine", "electric"] = "gas_turbine"
    electric_motor_efficiency: float = Field(default=0.95, gt=0, le=1)
    corrosion_allowance_m: float = Field(default=0.0, ge=0)
    max_d_over_t: float = Field(default=96.0, gt=10)
    wt_step_mm: float = Field(default=0.1, gt=0)
    coating: str = "3LPE"
    burial: SegmentInput | None = None
    wet_length_m: float = Field(default=0.0, ge=0, description="length needing buoyancy control")
    gross_calorific_value_kcal_scm: float = Field(default=9500.0, gt=0)
    concession: Concession = Concession()
    rates: UnitRates = Field(default_factory=placeholder_rates)


class WallDesign(_Model):
    location_class: LocationClass
    length_m: float
    wt_m: float
    governing: str
    process: str


class YearHydraulics(_Model):
    operating_year: int
    peak_flow: float
    stations: int
    peak_power_kw: float
    average_power_kw: float
    electricity_kwh: float
    fuel_gas_scm: float


class OptionResult(_Model):
    nps: str
    od_m: float
    grade: str
    feasible: bool
    issues: tuple[str, ...]
    walls: tuple[WallDesign, ...] = ()
    pipe_tonnes: float = 0.0
    concrete_coating_m3: float = 0.0
    capex: CapexEstimate | None = None
    hydraulics: tuple[YearHydraulics, ...] = ()
    opex: tuple[OpexBreakdown, ...] = ()
    cashflow: CashFlow | None = None
    throughput_unit: str = ""
    scm_per_mmbtu: float = 0.0

    @property
    def lcc_pv(self) -> float:
        return self.cashflow.lcc_pv if self.cashflow else math.inf

    @property
    def pv_capex(self) -> float:
        return self.cashflow.pv_capex if self.cashflow else math.inf

    @property
    def pv_opex(self) -> float:
        return self.cashflow.pv_opex if self.cashflow else math.inf

    @property
    def levelised_cost(self) -> float:
        """Per scm (gas) or per tonne (liquid)."""
        return self.cashflow.levelised_cost if self.cashflow else math.inf

    @property
    def levelised_cost_per_mmbtu(self) -> float:
        return self.levelised_cost * self.scm_per_mmbtu


def _wall(inp: OptionInput, od: float, smys: float, lc: LocationClass) -> tuple[float, str]:
    p = inp.design_pressure_pa_g
    probe = PipeSection(od_m=od, wt_m=od / 10, smys_pa=smys)
    req = pressure_design(
        probe, p, inp.service, lc, allowance=inp.corrosion_allowance_m
    ).required_wt_m
    governing = "hoop stress"
    if od / inp.max_d_over_t > req:
        req, governing = od / inp.max_d_over_t, f"D/t <= {inp.max_d_over_t:g}"
    step = inp.wt_step_mm / 1000
    wt = math.ceil(round(req / step, 9)) * step
    if inp.burial is None:
        return wt, governing
    # model_validate (not model_copy) so a misspelt field raises instead of being ignored
    seg = SegmentInput.model_validate(
        {
            **inp.burial.model_dump(),
            "service": inp.service,
            "location_class": lc,
            "design_pressure_pa": p,
            "corrosion_allowance_m": inp.corrosion_allowance_m,
            "buoyancy": None,
        }
    )
    for _ in range(400):
        a = assess_segment(PipeSection(od_m=od, wt_m=wt, smys_pa=smys), seg)
        fails = [c for c in a.checks if not c.passed]
        if not fails:
            return wt, governing
        governing = f"ALA: {max(fails, key=lambda c: c.utilisation).name}"
        wt += 5 * step
    raise ValueError(f"no wall up to {wt * 1000:.1f} mm passes the ALA checks")


def evaluate_option(inp: OptionInput) -> OptionResult:
    lib = default_library()
    size = lib.size(inp.nps)
    grade = lib.grade(inp.grade, psl=inp.psl)
    od = size.od_mm / 1000
    smys = grade.smys * 1e6
    rates = inp.rates
    c = inp.concession
    unit = "scm" if inp.service is Service.GAS else "t"
    base = {"nps": size.nps, "od_m": od, "grade": grade.name, "throughput_unit": unit}
    if len(inp.throughput) != c.operating_years:
        raise ValueError("throughput needs one value per operating year")

    # 1-2. walls and process per location class
    lengths: dict[LocationClass, float] = {}
    if inp.service is Service.GAS and inp.class_segments:
        for s in inp.class_segments:
            lengths[s.location_class] = lengths.get(s.location_class, 0.0) + s.length_m
    else:
        lengths[LocationClass.CLASS_1_DIV_2] = inp.profile.length_m
    walls: list[WallDesign] = []
    issues: list[str] = []
    for lc, length in lengths.items():
        if length <= 0:
            continue
        try:
            wt, gov = _wall(inp, od, smys, lc)
        except ValueError as e:
            return OptionResult(**base, feasible=False, issues=(str(e),))
        procs = lib.processes_for(size.od_mm, wt * 1000, psl=inp.psl)
        procs = [p for p in procs if p.code in rates.line_pipe.process_factor]
        if not procs:
            return OptionResult(
                **base,
                feasible=False,
                issues=(f"no process makes OD {size.od_mm} mm x {wt * 1000:.1f} mm",),
            )
        proc = min(procs, key=lambda p: rates.line_pipe.process_factor[p.code])
        walls.append(
            WallDesign(
                location_class=lc, length_m=length, wt_m=wt, governing=gov, process=proc.code
            )
        )
    segments = [
        PipeSegmentQty(
            length_m=w.length_m,
            od_m=od,
            wt_m=w.wt_m,
            grade=grade.name,
            process=w.process,
            location_class=w.location_class,
        )
        for w in walls
    ]
    tonnes = sum(s.tonnes for s in segments)

    # buoyancy control by concrete weight coating (ALA buoyancy check)
    cwc_m3 = 0.0
    if inp.wet_length_m:
        thinnest = min(walls, key=lambda w: w.wt_m)
        b_in = (
            inp.burial.buoyancy if inp.burial and inp.burial.buoyancy else None
        ) or BuoyancyInput()
        t_c = buoyancy(PipeSection(od_m=od, wt_m=thinnest.wt_m, smys_pa=smys), b_in)
        t = t_c.required_concrete_thickness_m
        if math.isinf(t):
            issues.append("concrete coating cannot provide negative buoyancy; use anchors")
        else:
            d_c = od + 2 * b_in.corrosion_coating_thickness_m
            cwc_m3 = math.pi / 4 * ((d_c + 2 * t) ** 2 - d_c**2) * inp.wet_length_m
    route = inp.route.model_copy(
        update={"concrete_coating_m3": inp.route.concrete_coating_m3 + cwc_m3}
    )

    # 3. hydraulics per year (thickest wall = smallest bore, conservative)
    id_m = od - 2 * max(w.wt_m for w in walls)
    cache: dict[tuple[float, float | None], tuple[int, float, float]] = {}

    def run(flow: float, supply: float | None) -> tuple[int, float, float]:
        key = (round(flow, 6), None if supply is None else round(supply, 1))
        if flow <= 0:
            return (0, 0.0, 0.0)
        if key not in cache:
            override: dict[str, float] = {"max_discharge_pa_g": inp.design_pressure_pa_g}
            if supply is not None:
                override["supply_pressure_pa_g"] = supply
            if inp.service is Service.GAS:
                st_in = inp.gas_stations or GasStationInput(
                    supply_pressure_pa_g=40e5, delivery_min_pa_g=30e5
                )
                st_in = GasStationInput.model_validate({**st_in.model_dump(), **override})
                r = gas_hydraulics(
                    inp.profile,
                    id_m,
                    Q_(flow, "MMSCMD"),
                    st_in,
                    inp.design_pressure_pa_g,
                    inp.gas,
                    offtakes=inp.offtakes,
                )
                fails = [ch.name for ch in r.checks if not ch.passed]
                cache[key] = (len(r.stations), r.total_power_w, r.fuel_gas_kg_s)
            else:
                pst = inp.pump_stations or PumpStationInput()
                pst = PumpStationInput.model_validate({**pst.model_dump(), **override})
                lr = liquid_hydraulics(
                    inp.profile,
                    id_m,
                    Q_(flow, "MMTPA"),
                    pst,
                    inp.design_pressure_pa_g,
                    inp.liquid,
                    offtakes=inp.offtakes,
                )
                fails = [ch.name for ch in lr.checks if not ch.passed]
                cache[key] = (len(lr.stations), lr.total_power_w, 0.0)
            if fails:
                raise HydraulicsError(f"{flow:g}: fails {', '.join(fails)}")
        return cache[key]

    supplies: tuple[float | None, ...] = (
        inp.supply_pressure_by_year
        if inp.supply_pressure_by_year is not None
        else (None,) * len(inp.throughput)
    )
    if len(supplies) != len(inp.throughput):
        raise ValueError("supply_pressure_by_year needs one value per operating year")

    years: list[YearHydraulics] = []
    try:
        for t, (q, sup) in enumerate(zip(inp.throughput, supplies, strict=True), start=1):
            n, peak_w, _ = run(q / inp.load_factor, sup)
            _, avg_w, avg_fuel = run(q, sup)
            if inp.service is Service.LIQUID:  # pump power is already electrical input
                kwh, scm = avg_w / 1000 * HOURS_PER_YEAR, 0.0
            elif inp.compressor_driver == "gas_turbine":
                kwh, scm = 0.0, avg_fuel * 3600 * HOURS_PER_YEAR / inp.gas.std_density
            else:  # electric-motor-driven compressors
                kwh, scm = avg_w / 1000 * HOURS_PER_YEAR / inp.electric_motor_efficiency, 0.0
            years.append(
                YearHydraulics(
                    operating_year=t,
                    peak_flow=q / inp.load_factor,
                    stations=n,
                    peak_power_kw=peak_w / 1000,
                    average_power_kw=avg_w / 1000,
                    electricity_kwh=kwh,
                    fuel_gas_scm=scm,
                )
            )
    except HydraulicsError as e:
        return OptionResult(
            **base,
            feasible=False,
            issues=(f"hydraulics at {e}",),
            walls=tuple(walls),
            pipe_tonnes=tonnes,
        )

    # 4. station phasing, CAPEX, OPEX, cash flow
    cum_n, cum_kw = years[0].stations, years[0].peak_power_kw
    capex = capex_estimate(
        inp.service,
        segments,
        route,
        StationQty(required_kw=cum_kw, count=cum_n),
        rates,
        inp.coating,
    )
    station_direct = sum(
        i.amount
        for i in station_items(inp.service, StationQty(required_kw=cum_kw, count=cum_n), rates)
    )
    sustaining: dict[int, float] = {}
    installed: list[tuple[int, float]] = []
    for y in years:
        dn = max(0, y.stations - cum_n)
        dkw = max(0.0, y.peak_power_kw - cum_kw)
        if (dn or dkw) and y.operating_year > 1:
            sr = rates.stations
            fixed, per_kw = (
                (sr.compressor_fixed, sr.compressor_per_kw)
                if inp.service is Service.GAS
                else (sr.pump_fixed, sr.pump_per_kw)
            )
            plant = dn * fixed + dkw * sr.installed_power_factor * per_kw
            land_cost = dn * rates.land.station_land_ha * rates.land.permanent_land_per_ha
            cal = c.operating_year_index(y.operating_year) - 1  # year before it is needed
            # indirects applied as in the CAPEX estimate (not via a total/direct ratio,
            # which mixed land into the overhead factor and was not linear in the rates)
            sustaining[cal] = sustaining.get(cal, 0.0) + with_indirects(plant, land_cost, rates)
            station_direct += plant + land_cost
        cum_n, cum_kw = max(cum_n, y.stations), max(cum_kw, y.peak_power_kw)
        installed.append((cum_n, station_direct))

    km = inp.profile.length_m / 1000
    opex: list[OpexBreakdown] = []
    cum_capex = capex.total
    for y, (n_inst, st_capex) in zip(years, installed, strict=True):
        cum_capex += sustaining.get(c.operating_year_index(y.operating_year) - 1, 0.0)
        opex.append(
            annual_opex(
                OpexYear(
                    operating_year=y.operating_year,
                    length_km=km,
                    stations=n_inst,
                    electricity_kwh=y.electricity_kwh,
                    fuel_gas_scm=y.fuel_gas_scm,
                    station_capex=st_capex,
                    cumulative_capex=cum_capex,
                ),
                rates,
            )
        )
    if inp.service is Service.GAS:
        quantity = [q * 1e6 * DAYS_PER_YEAR for q in inp.throughput]
    else:
        quantity = [q * 1e6 for q in inp.throughput]
    cf = build_cashflow(c, capex.total, [o.total for o in opex], quantity, sustaining)
    return OptionResult(
        **base,
        feasible=True,
        issues=tuple(issues),
        walls=tuple(walls),
        pipe_tonnes=tonnes,
        concrete_coating_m3=cwc_m3,
        capex=capex,
        hydraulics=tuple(years),
        opex=tuple(opex),
        cashflow=cf,
        scm_per_mmbtu=KCAL_PER_MMBTU / inp.gross_calorific_value_kcal_scm,
    )


def economic_diameter_curve(base: OptionInput, sizes: Iterable[str | float]) -> list[OptionResult]:
    """Evaluate the same option for several pipe sizes (the classic U-shaped LCC curve)."""
    return [evaluate_option(base.model_copy(update={"nps": n})) for n in sizes]


def best_option(results: Iterable[OptionResult]) -> OptionResult:
    feasible = [r for r in results if r.feasible]
    if not feasible:
        raise ValueError("no feasible option")
    return min(feasible, key=lambda r: r.lcc_pv)


SENSITIVITY_PARAMETERS = (
    "line pipe price",
    "construction rate",
    "station cost",
    "energy price",
    "throughput",
    "discount rate",
)


def _scaled(inp: OptionInput, name: str, factor: float) -> OptionInput:
    """Copy of ``inp`` with one sensitivity parameter scaled (validated rebuild)."""
    rates = inp.rates.model_dump()
    data = inp.model_dump()
    if name == "line pipe price":
        rates["line_pipe"]["base_rate_per_tonne"] *= factor
    elif name == "construction rate":
        rates["laying_per_inch_km"] *= factor
    elif name == "station cost":
        for k in ("compressor_fixed", "compressor_per_kw", "pump_fixed", "pump_per_kw"):
            rates["stations"][k] *= factor
    elif name == "energy price":
        rates["opex"]["electricity_per_kwh"] *= factor
        rates["opex"]["fuel_gas_per_scm"] *= factor
    elif name == "throughput":
        data["throughput"] = tuple(q * factor for q in inp.throughput)
    elif name == "discount rate":
        data["concession"]["discount_rate"] *= factor
    else:
        raise KeyError(f"unknown sensitivity parameter {name!r}")
    data["rates"] = rates
    return OptionInput.model_validate(data)


def option_tornado(
    inp: OptionInput,
    swing: float = 0.2,
    metric: Literal["lcc_pv", "levelised_cost"] = "levelised_cost",
    parameters: Iterable[str] = SENSITIVITY_PARAMETERS,
) -> list[tuple[str, float, float]]:
    """One-at-a-time +/- ``swing`` sensitivity of an option's ``metric``.

    Returns ``(parameter, value_at_low, value_at_high)``, largest swing first.
    Infeasible variants (e.g. higher throughput beyond the pipe's capacity)
    give ``inf``.
    """
    rows = []
    for name in parameters:
        lo = getattr(evaluate_option(_scaled(inp, name, 1 - swing)), metric)
        hi = getattr(evaluate_option(_scaled(inp, name, 1 + swing)), metric)
        rows.append((name, float(lo), float(hi)))

    def size(r: tuple[str, float, float]) -> float:
        d = abs(r[2] - r[1])
        return math.inf if math.isnan(d) else d

    return sorted(rows, key=size, reverse=True)
