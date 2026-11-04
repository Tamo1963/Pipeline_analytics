"""Unit handling for the whole suite.

Every physical quantity that crosses a module boundary is a ``pint`` Quantity
from the single shared registry ``ureg``. Mixing registries raises errors in
pint, so always import ``ureg``/``Q_`` from here.

Pipeline-industry units added on top of pint's defaults:

* ``scm``        standard cubic metre (volume at reference conditions; the
                 conditions themselves are carried by the study, not the unit)
* ``MMSCMD``     million standard cubic metres per day (Indian gas practice)
* ``MMSCFD``     million standard cubic feet per day
* ``MMTPA``      million tonnes per annum (``year`` = 365.25 days)
* ``bpd``        oil barrels per day
* ``kgf_cm2``    kgf/cm², still common on Indian drawings and datasheets

Gauge pressures (barg, psig, kg/cm²g) are offsets from atmospheric pressure and
are deliberately *not* units here: convert with :func:`gauge_to_absolute` and
:func:`absolute_to_gauge` so the atmospheric pressure used is explicit.
"""

from __future__ import annotations

from typing import Any

import pint

ureg: Any = pint.UnitRegistry()
Q_: Any = ureg.Quantity

for _definition in (
    "standard_cubic_meter = meter ** 3 = scm",
    "MMSCMD = 1e6 * scm / day",
    "MMSCFD = 1e6 * foot ** 3 / day",
    "MMTPA = 1e6 * metric_ton / year",
    "bpd = oil_barrel / day",
    "kgf_cm2 = kilogram_force / centimeter ** 2",
):
    ureg.define(_definition)

STANDARD_ATMOSPHERE = Q_(1.01325, "bar")


class UnitError(ValueError):
    """Raised when a quantity has the wrong dimensionality for its use."""


def as_quantity(value: Any, default_unit: str) -> Any:
    """Return ``value`` as a Quantity, attaching ``default_unit`` to bare numbers.

    Quantities are checked for compatibility with ``default_unit`` and returned
    unchanged (not converted) so callers keep the user's preferred unit.
    """
    if isinstance(value, ureg.Quantity):
        if not value.is_compatible_with(default_unit):
            raise UnitError(f"{value:~P} is not compatible with {default_unit}")
        return value
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise UnitError(f"expected a number or Quantity, got {type(value).__name__}")
    return Q_(float(value), default_unit)


def magnitude_in(value: Any, unit: str) -> float:
    """Magnitude of ``value`` expressed in ``unit`` (bare numbers are assumed in ``unit``)."""
    return float(as_quantity(value, unit).to(unit).magnitude)


def gauge_to_absolute(p_gauge: Any, atmosphere: Any = STANDARD_ATMOSPHERE) -> Any:
    """Convert a gauge pressure to absolute: ``p_abs = p_gauge + p_atm``."""
    p = as_quantity(p_gauge, "bar")
    atm = as_quantity(atmosphere, "bar")
    return (p + atm.to(p.units)).to(p.units)


def absolute_to_gauge(p_absolute: Any, atmosphere: Any = STANDARD_ATMOSPHERE) -> Any:
    """Convert an absolute pressure to gauge: ``p_gauge = p_abs - p_atm``."""
    p = as_quantity(p_absolute, "bar")
    atm = as_quantity(atmosphere, "bar")
    return (p - atm.to(p.units)).to(p.units)
