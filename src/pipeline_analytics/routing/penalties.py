"""OPEX-equivalent route penalties, so the router trades CAPEX against energy.

* **Length** (INR per metre of route, used by the router): present value (PV)
  of line O&M per metre plus friction energy per metre. Friction work per kg
  per metre is ``f v^2 / (2 D)``; for gas, v is taken at
  ``mean_pressure_fraction`` x design pressure. This is path-additive and
  valid, so it goes on graph edges (``CostSurfaceParams.length_per_m``).

* **Lift** (INR per metre of *net* rise, reported only). Lifting a mass flow m
  through dh needs shaft work m g dh. In a packed line, gas or liquid, the
  pressure spent climbing is recovered on the way down. Energy therefore
  depends on the net elevation change between the end points, which is the
  same for every route between fixed end points. So it is NOT a routing
  penalty. A gross-climb penalty (descent free) would push routes away from
  hills for an energy cost that does not exist.

  Elevation does matter through high points that control the pump discharge
  (liquid slack-line) and through steep-terrain construction cost. The terrain
  factors in the cost surface cover the latter. The former is judged by the
  full hydraulic and lifecycle evaluation of each corridor
  (``RouteExtraction.option_input`` -> ``evaluate_option``).
  ``CostSurfaceParams.climb_per_m`` stays available as an explicit heuristic
  (default 0).

PV factor: sum over operating years of (1 + r)^-(construction years + t), using
the average throughput over the concession.
"""

from __future__ import annotations

from dataclasses import dataclass

from pipeline_analytics.core.jurisdiction import Service
from pipeline_analytics.core.units import Q_
from pipeline_analytics.economics.option import OptionInput
from pipeline_analytics.hydraulics.fluids import P_ATM, pipe_area
from pipeline_analytics.hydraulics.friction import darcy_friction, reynolds
from pipeline_analytics.hydraulics.gas import DEFAULT_ROUGHNESS_M
from pipeline_analytics.hydraulics.liquid import volumetric_flow_m3_s
from pipeline_analytics.mechanical.common import G
from pipeline_analytics.mechanical.pressure import LocationClass
from pipeline_analytics.pipespec import default_library
from pipeline_analytics.routing.cost_surface import CostSurfaceParams, wall_for_class

SECONDS_PER_YEAR = 3600 * 8760


@dataclass(frozen=True)
class OpexPenalties:
    length_per_m: float
    """Use as CostSurfaceParams.length_per_m."""
    lift_per_m_net: float
    """PV of lifting the flow by 1 m net (end - start); informational, not a routing penalty."""
    mass_flow_kg_s: float
    price_per_joule: float
    pv_factor: float


def pv_factor(inp: OptionInput) -> float:
    c = inp.concession
    r = c.discount_rate
    return sum((1 + r) ** -(c.construction_years + t) for t in range(1, c.operating_years + 1))


def opex_penalties(inp: OptionInput, mean_pressure_fraction: float = 0.8) -> OpexPenalties:
    rates = inp.rates
    o = rates.opex
    q_avg = sum(inp.throughput) / len(inp.throughput)
    lib = default_library()
    od = lib.size(inp.nps).od_mm / 1000
    smys = lib.grade(inp.grade, psl=inp.psl).smys * 1e6
    params = CostSurfaceParams(
        service=inp.service,
        nps=inp.nps,
        grade=inp.grade,
        psl=inp.psl,
        design_pressure_pa_g=inp.design_pressure_pa_g,
        corrosion_allowance_m=inp.corrosion_allowance_m,
        rates=rates,
    )
    id_m = od - 2 * wall_for_class(params, od, smys, LocationClass.CLASS_1_DIV_2)
    area = pipe_area(id_m)

    if inp.service is Service.GAS:
        gas = inp.gas
        m = Q_(q_avg, "MMSCMD").to("m**3/s").magnitude * gas.std_density
        st = inp.gas_stations
        eta = st.compressor_efficiency if st else 0.80
        if inp.compressor_driver == "gas_turbine":
            driver = st.driver_efficiency if st else 0.33
            price = o.fuel_gas_per_scm / (driver * gas.lower_heating_value_j_kg * gas.std_density)
        else:
            price = o.electricity_per_kwh / 3.6e6 / inp.electric_motor_efficiency
        rho = gas.density(mean_pressure_fraction * inp.design_pressure_pa_g + P_ATM)
        mu = gas.viscosity_pa_s
    else:
        liq = inp.liquid
        q = volumetric_flow_m3_s(Q_(q_avg, "MMTPA"), liq)
        m = q * liq.density_kg_m3
        pst = inp.pump_stations
        eta = (pst.pump_efficiency * pst.motor_efficiency) if pst else 0.75 * 0.95
        price = o.electricity_per_kwh / 3.6e6
        rho, mu = liq.density_kg_m3, liq.dynamic_viscosity_pa_s

    pvf = pv_factor(inp)
    v = m / (rho * area)
    f = darcy_friction(reynolds(m, id_m, mu), DEFAULT_ROUGHNESS_M / id_m)
    friction_w_per_m = m * f * v**2 / (2 * id_m) / eta
    lift = m * G / eta * price * SECONDS_PER_YEAR * pvf
    length = (friction_w_per_m * price * SECONDS_PER_YEAR + o.line_om_per_km_year / 1000) * pvf
    return OpexPenalties(
        length_per_m=length,
        lift_per_m_net=lift,
        mass_flow_kg_s=m,
        price_per_joule=price,
        pv_factor=pvf,
    )
