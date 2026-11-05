"""Concession cash flows and lifecycle economics.

Conventions
-----------
* Years are numbered from 1. Construction years come first, then operating
  years; the concession ends after the last operating year.
* End-of-year discounting: a cash flow in year t is worth ``CF / (1 + r)^t``
  at the start of year 1 (financial close).
* Real terms by default (constant base-year prices, real discount rate).
  Escalation per cost group can be added to make a nominal model.

Metrics
-------
* ``lcc_pv``: present value of CAPEX + OPEX - residual (handback) value. This
  is the objective the feasibility study minimises.
* ``levelised_cost``: lcc_pv / PV(throughput), the constant tariff that
  recovers all costs at the discount rate (pre-tax).
* ``tariff_for_return``: constant tariff giving a target post-tax return with
  straight-line depreciation and loss carry-forward. PNGRB-style cost-plus
  tariffs use a 12 % post-tax return on capital employed.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from pydantic import Field, model_validator

from pipeline_analytics.cost.rates import _Model


class Concession(_Model):
    construction_years: int = Field(default=3, ge=1, le=10)
    capex_phasing: tuple[float, ...] = (0.25, 0.45, 0.30)
    operating_years: int = Field(default=25, ge=1, le=60)
    discount_rate: float = Field(default=0.10, ge=0, lt=1)
    residual_value_fraction: float = Field(
        default=0.0, ge=0, le=1, description="of initial CAPEX, received at handback"
    )
    tax_rate: float = Field(default=0.2517, ge=0, lt=1, description="India corporate (new regime)")
    depreciation_years: int | None = Field(default=None, ge=1, description="default: operating")

    @model_validator(mode="after")
    def _check(self) -> Concession:
        if len(self.capex_phasing) != self.construction_years:
            raise ValueError("capex_phasing needs one share per construction year")
        if abs(sum(self.capex_phasing) - 1) > 1e-9:
            raise ValueError("capex_phasing must sum to 1")
        return self

    @property
    def total_years(self) -> int:
        return self.construction_years + self.operating_years

    def operating_year_index(self, operating_year: int) -> int:
        """Calendar index (1-based) of operating year 1, 2, ..."""
        return self.construction_years + operating_year


class CashFlowYear(_Model):
    year: int
    operating: bool
    capex: float = 0.0
    opex: float = 0.0
    residual: float = 0.0
    throughput: float = 0.0
    """Transported quantity in the year (e.g. scm or tonnes)."""


def _pv(values: Sequence[float], rate: float) -> float:
    return sum(v / (1 + rate) ** (t + 1) for t, v in enumerate(values))


def irr(cash_flows: Sequence[float], lo: float = -0.99, hi: float = 2.0) -> float:
    """Internal rate of return by bisection (cash flows from year 1)."""

    def npv(r: float) -> float:
        return _pv(cash_flows, r)

    f_lo, f_hi = npv(lo), npv(hi)
    if f_lo * f_hi > 0:
        raise ValueError("IRR not bracketed: cash flows may not change sign")
    for _ in range(200):
        mid = (lo + hi) / 2
        f_mid = npv(mid)
        if (f_mid > 0) == (f_lo > 0):
            lo, f_lo = mid, f_mid
        else:
            hi = mid
    return (lo + hi) / 2


class CashFlow(_Model):
    concession: Concession
    years: tuple[CashFlowYear, ...]

    @model_validator(mode="after")
    def _check(self) -> CashFlow:
        if [y.year for y in self.years] != list(range(1, self.concession.total_years + 1)):
            raise ValueError("cash flow must cover every concession year in order")
        return self

    def _col(self, name: str) -> list[float]:
        return [float(getattr(y, name)) for y in self.years]

    @property
    def rate(self) -> float:
        return self.concession.discount_rate

    @property
    def pv_capex(self) -> float:
        return _pv(self._col("capex"), self.rate)

    @property
    def pv_opex(self) -> float:
        return _pv(self._col("opex"), self.rate)

    @property
    def pv_residual(self) -> float:
        return _pv(self._col("residual"), self.rate)

    @property
    def pv_throughput(self) -> float:
        return _pv(self._col("throughput"), self.rate)

    @property
    def lcc_pv(self) -> float:
        return self.pv_capex + self.pv_opex - self.pv_residual

    @property
    def levelised_cost(self) -> float:
        """Pre-tax cost per unit of throughput at the discount rate."""
        return self.lcc_pv / self.pv_throughput

    @property
    def total_capex(self) -> float:
        return sum(self._col("capex"))

    def pre_tax_flows(self, tariff: float) -> list[float]:
        return [y.throughput * tariff - y.capex - y.opex + y.residual for y in self.years]

    def post_tax_flows(self, tariff: float) -> list[float]:
        """Cash flows after corporate tax, straight-line depreciation and loss carry-forward."""
        c = self.concession
        dep_years = c.depreciation_years or c.operating_years
        first_op = c.construction_years + 1
        dep = [0.0] * len(self.years)
        for y in self.years:
            if not y.capex:
                continue
            start = max(y.year + 1, first_op)
            for t in range(start, min(start + dep_years, c.total_years + 1)):
                dep[t - 1] += y.capex / dep_years
        flows, loss = [], 0.0
        for i, y in enumerate(self.years):
            taxable = y.throughput * tariff - y.opex - dep[i] + loss
            tax = c.tax_rate * taxable if taxable > 0 else 0.0
            loss = min(taxable, 0.0)
            flows.append(y.throughput * tariff - y.capex - y.opex + y.residual - tax)
        return flows

    def npv(self, tariff: float, rate: float | None = None, post_tax: bool = False) -> float:
        flows = self.post_tax_flows(tariff) if post_tax else self.pre_tax_flows(tariff)
        return _pv(flows, self.rate if rate is None else rate)

    def irr(self, tariff: float, post_tax: bool = True) -> float:
        return irr(self.post_tax_flows(tariff) if post_tax else self.pre_tax_flows(tariff))

    def tariff_for_return(self, target_return: float = 0.12, post_tax: bool = True) -> float:
        """Constant tariff (per unit throughput) giving NPV = 0 at ``target_return``."""
        hi = 1.0
        while self.npv(hi, target_return, post_tax) < 0:
            hi *= 2
            if hi > 1e12:  # pragma: no cover
                raise ValueError("no tariff recovers the costs")
        lo = 0.0
        for _ in range(200):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if self.npv(mid, target_return, post_tax) < 0 else (lo, mid)
        return hi


def build_cashflow(
    concession: Concession,
    initial_capex: float,
    opex_by_op_year: Sequence[float],
    throughput_by_op_year: Sequence[float],
    sustaining_capex_by_year: dict[int, float] | None = None,
) -> CashFlow:
    """Assemble a concession cash flow.

    ``initial_capex`` is spread over construction years by ``capex_phasing``.
    ``sustaining_capex_by_year`` maps calendar year (1-based) to extra CAPEX,
    e.g. stations added as throughput ramps up. The residual value is a fraction
    of initial CAPEX received in the last year.
    """
    c = concession
    if len(opex_by_op_year) != c.operating_years or len(throughput_by_op_year) != c.operating_years:
        raise ValueError("opex and throughput need one value per operating year")
    extra = sustaining_capex_by_year or {}
    years = []
    for t in range(1, c.total_years + 1):
        op = t > c.construction_years
        k = t - c.construction_years - 1
        years.append(
            CashFlowYear(
                year=t,
                operating=op,
                capex=(initial_capex * c.capex_phasing[t - 1] if not op else 0.0)
                + extra.get(t, 0.0),
                opex=opex_by_op_year[k] if op else 0.0,
                throughput=throughput_by_op_year[k] if op else 0.0,
                residual=c.residual_value_fraction * initial_capex if t == c.total_years else 0.0,
            )
        )
    return CashFlow(concession=c, years=tuple(years))


def tornado(
    evaluate: Callable[[dict[str, float]], float],
    base: dict[str, float],
    swing: float | dict[str, float] = 0.2,
) -> list[tuple[str, float, float]]:
    """One-at-a-time sensitivity.

    Each parameter is multiplied by (1 - swing) and (1 + swing). Returns
    ``(name, value_low, value_high)`` sorted by the size of the swing, largest
    first.
    """
    out = []
    for name, value in base.items():
        s = swing[name] if isinstance(swing, dict) else swing
        lo = evaluate({**base, name: value * (1 - s)})
        hi = evaluate({**base, name: value * (1 + s)})
        out.append((name, lo, hi))
    return sorted(out, key=lambda r: abs(r[2] - r[1]), reverse=True)
