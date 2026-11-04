"""Steady-state liquid and gas pipeline hydraulics with pump/compressor station siting.

>>> from pipeline_analytics.hydraulics import Profile, liquid_pressure_profile
>>> p = liquid_pressure_profile(Profile.flat(10_000), 0.3, 0.1, 50e5)
>>> p[0] > p[-1]
True
"""

from pipeline_analytics.hydraulics.fluids import Gas, Liquid
from pipeline_analytics.hydraulics.friction import colebrook, darcy_friction, reynolds, swamee_jain
from pipeline_analytics.hydraulics.gas import (
    GasHydraulicsResult,
    GasStationInput,
    compressor_power_w,
    gas_hydraulics,
    gas_pressure_profile,
)
from pipeline_analytics.hydraulics.liquid import (
    LiquidHydraulicsResult,
    PumpStationInput,
    liquid_hydraulics,
    liquid_pressure_profile,
    pump_power_w,
)
from pipeline_analytics.hydraulics.profile import Profile
from pipeline_analytics.hydraulics.stations import HydraulicsError, Station

__all__ = [
    "Gas",
    "GasHydraulicsResult",
    "GasStationInput",
    "HydraulicsError",
    "Liquid",
    "LiquidHydraulicsResult",
    "Profile",
    "PumpStationInput",
    "Station",
    "colebrook",
    "compressor_power_w",
    "darcy_friction",
    "gas_hydraulics",
    "gas_pressure_profile",
    "liquid_hydraulics",
    "liquid_pressure_profile",
    "pump_power_w",
    "reynolds",
    "swamee_jain",
]
