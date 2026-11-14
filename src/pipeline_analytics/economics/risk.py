"""Monte Carlo risk analysis of lifecycle cost (P10 / P50 / P90).

Method
------
For a fixed design (size, grade, MAOP), walls, stations and process choice do
not depend on unit rates, so lifecycle cost (LCC) is *exactly linear* in each
rate group's factor (see ``cost.rates.RATE_GROUPS``)::

    LCC(k) = LCC_0 + sum_g (k_g - 1) * D_g,     D_g = LCC(k_g = 2) - LCC_0

So each design needs only 1 + G full evaluations per throughput scenario,
after which any number of samples cost nothing. ``check_linearity`` verifies
this against direct evaluation.

Throughput uncertainty changes the hydraulics (stations, power), so it is a
discrete set of weighted scenarios, each fully evaluated. A design that is
infeasible in a scenario (e.g. velocity at high flow) has infinite LCC in
those samples and is reported with its probability of being infeasible.

All designs share the same random samples (common random numbers), so the
**probability that each design is cheapest** is a fair comparison.

Rate groups are sampled independently from triangular distributions; real
markets correlate (steel and construction often move together), so treat the
spread as indicative.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from typing import Any

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from pipeline_analytics.cost.rates import RATE_GROUPS, scale_rate_groups
from pipeline_analytics.economics.option import OptionInput, OptionResult, evaluate_option


class _M(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", ser_json_inf_nan="constants")


class Triangular(_M):
    low: float = Field(gt=0)
    mode: float = Field(gt=0)
    high: float = Field(gt=0)

    @model_validator(mode="after")
    def _check(self) -> Triangular:
        if not self.low <= self.mode <= self.high or self.low == self.high:
            raise ValueError("need low <= mode <= high with low < high")
        return self

    def sample(self, rng: np.random.Generator, n: int) -> np.ndarray:
        return rng.triangular(self.low, self.mode, self.high, n)

    @property
    def mean(self) -> float:
        return (self.low + self.mode + self.high) / 3


class ThroughputScenario(_M):
    name: str
    probability: float = Field(gt=0, le=1)
    factor: float = Field(gt=0, description="multiplies every year's throughput")


def default_groups() -> dict[str, Triangular]:
    """Screening-level ranges (factors on the unit rates); replace with project judgement."""
    return {
        "line_pipe": Triangular(low=0.85, mode=1.0, high=1.30),
        "construction": Triangular(low=0.85, mode=1.0, high=1.40),
        "stations": Triangular(low=0.85, mode=1.0, high=1.30),
        "other_capex": Triangular(low=0.90, mode=1.0, high=1.25),
        "energy": Triangular(low=0.70, mode=1.0, high=1.50),
        "o_and_m": Triangular(low=0.90, mode=1.0, high=1.20),
    }


def default_scenarios() -> list[ThroughputScenario]:
    return [
        ThroughputScenario(name="low", probability=0.25, factor=0.8),
        ThroughputScenario(name="base", probability=0.5, factor=1.0),
        ThroughputScenario(name="high", probability=0.25, factor=1.2),
    ]


class RiskInputs(_M):
    groups: dict[str, Triangular] = Field(default_factory=default_groups)
    scenarios: list[ThroughputScenario] = Field(default_factory=default_scenarios)
    samples: int = Field(default=10_000, ge=100, le=1_000_000)
    seed: int = 1

    @model_validator(mode="after")
    def _check(self) -> RiskInputs:
        unknown = set(self.groups) - set(RATE_GROUPS)
        if unknown:
            raise ValueError(f"unknown rate groups: {sorted(unknown)}")
        if abs(sum(s.probability for s in self.scenarios) - 1) > 1e-9:
            raise ValueError("scenario probabilities must sum to 1")
        return self


class LinearModel(_M):
    """LCC and PV throughput of one design in one scenario, linear in the rate factors."""

    feasible: bool
    lcc0: float
    pv_throughput: float
    sensitivities: dict[str, float]
    """D_g: change in LCC (INR) when group g goes from factor 1 to factor 2."""

    def lcc(self, factors: dict[str, float]) -> float:
        if not self.feasible:
            return math.inf
        return self.lcc0 + sum((factors.get(g, 1.0) - 1) * d for g, d in self.sensitivities.items())


class DesignRisk(_M):
    label: str
    lcc_p10: float
    lcc_p50: float
    lcc_p90: float
    lcc_mean: float
    levelised_p10: float
    levelised_p50: float
    levelised_p90: float
    prob_cheapest: float
    prob_infeasible: float
    deterministic_lcc: float
    """Base scenario, all factors 1 (the deterministic study result)."""


class RiskResult(_M):
    inputs: RiskInputs
    designs: list[DesignRisk]
    evaluations: int
    unit: str = "INR"

    def best_by_p50(self) -> DesignRisk:
        return min(self.designs, key=lambda d: d.lcc_p50)


def _variant(inp: OptionInput, factor: float, rate_factors: dict[str, float]) -> OptionInput:
    d = inp.model_dump()
    d["throughput"] = [q * factor for q in inp.throughput]
    d["rates"] = scale_rate_groups(inp.rates, rate_factors).model_dump()
    return OptionInput.model_validate(d)


def label_of(inp: OptionInput) -> str:
    return f'{inp.nps}" {inp.grade} @ {inp.design_pressure_pa_g / 1e5:.1f} barg'


def build_linear_models(
    designs: Sequence[OptionInput], inputs: RiskInputs, workers: int = 1
) -> tuple[list[list[LinearModel]], int]:
    """``models[design][scenario]`` from 1 + G evaluations per pair."""
    groups = list(inputs.groups)
    jobs: list[OptionInput] = []
    for inp in designs:
        for sc in inputs.scenarios:
            jobs.append(_variant(inp, sc.factor, {}))
            jobs.extend(_variant(inp, sc.factor, {g: 2.0}) for g in groups)
    if workers > 1:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            results: list[OptionResult] = list(pool.map(evaluate_option, jobs))
    else:
        results = [evaluate_option(j) for j in jobs]
    it = iter(results)
    models: list[list[LinearModel]] = []
    for _ in designs:
        row = []
        for _sc in inputs.scenarios:
            base = next(it)
            bumped = [next(it) for _ in groups]
            if not base.feasible or base.cashflow is None:
                row.append(LinearModel(feasible=False, lcc0=math.inf, pv_throughput=1.0,
                                       sensitivities={}))  # fmt: skip
                continue
            row.append(
                LinearModel(
                    feasible=True,
                    lcc0=base.lcc_pv,
                    pv_throughput=base.cashflow.pv_throughput,
                    sensitivities={
                        g: b.lcc_pv - base.lcc_pv for g, b in zip(groups, bumped, strict=True)
                    },
                )
            )
        models.append(row)
    return models, len(jobs)


def monte_carlo(
    designs: Sequence[OptionInput],
    inputs: RiskInputs | None = None,
    *,
    workers: int = 1,
    per_unit: Sequence[float] | None = None,
) -> RiskResult:
    """Sample lifecycle cost for each design under rate and throughput uncertainty.

    ``per_unit`` converts levelised cost per throughput unit for reporting (e.g.
    scm per MMBtu for gas); defaults to 1.
    """
    inputs = inputs or RiskInputs()
    if not designs:
        raise ValueError("at least one design is needed")
    models, n_eval = build_linear_models(designs, inputs, workers)
    rng = np.random.default_rng(inputs.seed)
    n = inputs.samples
    probs = np.array([s.probability for s in inputs.scenarios])
    scen = rng.choice(len(inputs.scenarios), size=n, p=probs)
    k = {g: dist.sample(rng, n) for g, dist in inputs.groups.items()}
    lcc = np.empty((len(designs), n))
    lev = np.empty((len(designs), n))
    for di, row in enumerate(models):
        for si, m in enumerate(row):
            mask = scen == si
            if not m.feasible:
                lcc[di, mask] = np.inf
                lev[di, mask] = np.inf
                continue
            val = np.full(mask.sum(), m.lcc0)
            for g, d in m.sensitivities.items():
                val += (k[g][mask] - 1) * d
            lcc[di, mask] = val
            lev[di, mask] = val / m.pv_throughput
    cheapest = np.argmin(lcc, axis=0)
    scale = list(per_unit) if per_unit is not None else [1.0] * len(designs)
    base_idx = next((i for i, s in enumerate(inputs.scenarios) if s.factor == 1.0), 0)
    out = []
    for di, inp in enumerate(designs):
        x = lcc[di]
        finite = np.isfinite(x)
        # percentiles over all samples: infeasible samples count as +inf (worst)
        p10, p50, p90 = (float(v) for v in np.percentile(x, [10, 50, 90], method="lower"))
        lv = lev[di] * scale[di]
        l10, l50, l90 = (float(v) for v in np.percentile(lv, [10, 50, 90], method="lower"))
        out.append(
            DesignRisk(
                label=label_of(inp),
                lcc_p10=p10,
                lcc_p50=p50,
                lcc_p90=p90,
                lcc_mean=float(x[finite].mean()) if finite.all() else math.inf,
                levelised_p10=l10,
                levelised_p50=l50,
                levelised_p90=l90,
                prob_cheapest=float((cheapest == di).mean()),
                prob_infeasible=float(1 - finite.mean()),
                deterministic_lcc=models[di][base_idx].lcc0,
            )
        )
    return RiskResult(inputs=inputs, designs=out, evaluations=n_eval)


def check_linearity(
    inp: OptionInput, factors: dict[str, float], inputs: RiskInputs | None = None
) -> tuple[float, float]:
    """(linear-model LCC, directly evaluated LCC) for one set of factors (base throughput)."""
    inputs = inputs or RiskInputs(
        scenarios=[ThroughputScenario(name="base", probability=1, factor=1)]
    )
    models, _ = build_linear_models([inp], inputs)
    direct = evaluate_option(_variant(inp, 1.0, factors))
    return models[0][0].lcc(factors), direct.lcc_pv


def summary_rows(result: RiskResult, money_per_inr: float = 1.0) -> list[dict[str, Any]]:
    return [
        {
            "design": d.label,
            "p10": d.lcc_p10 * money_per_inr,
            "p50": d.lcc_p50 * money_per_inr,
            "p90": d.lcc_p90 * money_per_inr,
            "deterministic": d.deterministic_lcc * money_per_inr,
            "prob_cheapest": d.prob_cheapest,
            "prob_infeasible": d.prob_infeasible,
        }
        for d in result.designs
    ]
