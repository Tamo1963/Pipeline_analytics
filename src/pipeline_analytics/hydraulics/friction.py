"""Pipe friction factors (Darcy-Weisbach convention, f = 4 x Fanning)."""

from __future__ import annotations

import math


def reynolds(mass_flow_kg_s: float, id_m: float, viscosity_pa_s: float) -> float:
    """Re = rho v D / mu = 4 m_dot / (pi D mu)."""
    return 4 * abs(mass_flow_kg_s) / (math.pi * id_m * viscosity_pa_s)


def swamee_jain(re: float, relative_roughness: float) -> float:
    """Explicit approximation to Colebrook (within about 1 %)."""
    return 0.25 / math.log10(relative_roughness / 3.7 + 5.74 / re**0.9) ** 2


def colebrook(re: float, relative_roughness: float, tol: float = 1e-12) -> float:
    """Colebrook-White, solved by fixed-point iteration on 1/sqrt(f)."""
    x = 1 / math.sqrt(swamee_jain(re, relative_roughness))
    for _ in range(100):
        x_new = -2 * math.log10(relative_roughness / 3.7 + 2.51 * x / re)
        if abs(x_new - x) < tol:
            break
        x = x_new
    return 1 / x_new**2


def darcy_friction(re: float, relative_roughness: float) -> float:
    """Darcy friction factor: laminar 64/Re below Re 2300, else Colebrook.

    The transition zone (2300-4000) uses Colebrook, which is conservative for
    pipeline design.
    """
    if re <= 0:
        raise ValueError("Reynolds number must be positive")
    if re < 2300:
        return 64 / re
    return colebrook(re, relative_roughness)
