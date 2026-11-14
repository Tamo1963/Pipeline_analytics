"""Fluid properties for pipeline hydraulics.

Gas: real-gas behaviour from specific gravity using Standing's pseudo-critical
correlation and the Papay compressibility equation (screening accuracy, valid
for lean natural gas at about 1.2 < Tr < 3 and Pr < 15). Detailed design should
use AGA-8 / GERG-2008 from a gas composition.
"""

from __future__ import annotations

import math

from pydantic import Field

from pipeline_analytics.core.units import STANDARD_ATMOSPHERE
from pipeline_analytics.mechanical.common import _Frozen

R_UNIVERSAL = 8.314462618  # J/(mol K)
M_AIR = 28.9647e-3  # kg/mol
P_ATM = float(STANDARD_ATMOSPHERE.to("Pa").magnitude)


class Gas(_Frozen):
    """Natural gas described by specific gravity (air = 1)."""

    specific_gravity: float = Field(default=0.60, gt=0.5, lt=1.0)
    flowing_temperature_c: float = Field(default=25.0, gt=-50, lt=150)
    viscosity_pa_s: float = Field(default=1.1e-5, gt=0)
    heat_capacity_ratio: float = Field(default=1.30, gt=1.0, lt=1.67)
    # Standard reference conditions (ISO 13443): 15 degC, 101.325 kPa.
    std_temperature_c: float = 15.0
    std_pressure_pa: float = 101_325.0
    lower_heating_value_j_kg: float = Field(default=47e6, gt=0)

    @property
    def molar_mass(self) -> float:
        return self.specific_gravity * M_AIR

    @property
    def flowing_temperature_k(self) -> float:
        return self.flowing_temperature_c + 273.15

    def pseudo_critical(self) -> tuple[float, float]:
        """(T_pc K, P_pc Pa) from Standing's correlation for natural gas."""
        g = self.specific_gravity
        t_pc_r = 168 + 325 * g - 12.5 * g**2
        p_pc_psia = 677 + 15 * g - 37.5 * g**2
        return t_pc_r * 5 / 9, p_pc_psia * 6894.757

    def z(self, p_abs_pa: float, t_k: float | None = None) -> float:
        """Papay: Z = 1 - 3.52 Pr / 10^(0.9813 Tr) + 0.274 Pr^2 / 10^(0.8157 Tr)."""
        t = t_k if t_k is not None else self.flowing_temperature_k
        t_pc, p_pc = self.pseudo_critical()
        pr, tr = p_abs_pa / p_pc, t / t_pc
        return 1 - 3.52 * pr / 10 ** (0.9813 * tr) + 0.274 * pr**2 / 10 ** (0.8157 * tr)

    def density(self, p_abs_pa: float, t_k: float | None = None) -> float:
        t = t_k if t_k is not None else self.flowing_temperature_k
        return p_abs_pa * self.molar_mass / (self.z(p_abs_pa, t) * R_UNIVERSAL * t)

    @property
    def std_density(self) -> float:
        """Density at standard reference conditions (Z taken from Papay, ~0.998)."""
        return self.density(self.std_pressure_pa, self.std_temperature_c + 273.15)


class Liquid(_Frozen):
    """Liquid hydrocarbon (crude oil or product) at flowing conditions."""

    density_kg_m3: float = Field(default=850.0, gt=300, lt=1300)
    kinematic_viscosity_m2_s: float = Field(default=10e-6, gt=0, description="10 cSt default")
    vapour_pressure_pa_abs: float = Field(default=50e3, ge=0)

    @property
    def dynamic_viscosity_pa_s(self) -> float:
        return self.kinematic_viscosity_m2_s * self.density_kg_m3


def to_abs(p_gauge_pa: float) -> float:
    return p_gauge_pa + P_ATM


def to_gauge(p_abs_pa: float) -> float:
    return p_abs_pa - P_ATM


def pipe_area(id_m: float) -> float:
    return math.pi / 4 * id_m**2
