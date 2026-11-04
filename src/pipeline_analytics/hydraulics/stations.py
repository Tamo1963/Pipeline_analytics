"""March along a profile and site pump / compressor stations.

Both liquid and gas models provide ``step(p_abs, k) -> p_abs`` for the
segment from point k to k+1 (NaN when the flow cannot be pushed through).

Siting (greedy, the usual screening method):
1. The originating station at chainage 0 discharges at its cap: the lowest of
   the station discharge limit, the MAOP at that point and (for compressors)
   the maximum compression ratio times suction.
2. March downstream. When the next point would fall below the minimum line
   pressure, or the end would miss the delivery pressure, put a station at the
   last point that still had at least the minimum suction pressure.
3. Repeat from that station.

Then each leg's discharge is trimmed (bisection) to the lowest pressure that
still meets the downstream minimums. This gives realistic power, and drops
stations that need no boost. Stations sit on profile points, so resample the
profile (e.g. 1 km) for finer siting.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence

from pipeline_analytics.hydraulics.fluids import to_abs, to_gauge
from pipeline_analytics.hydraulics.profile import Profile
from pipeline_analytics.mechanical.common import CheckResult, _Frozen

Step = Callable[[float, int], float]
PowerFn = Callable[[float, float], float]


class HydraulicsError(ValueError):
    """The flow cannot be delivered with the given pipe and limits."""


class Station(_Frozen):
    index: int
    chainage_m: float
    suction_pa_g: float
    discharge_pa_g: float
    power_w: float

    @property
    def ratio(self) -> float:
        return to_abs(self.discharge_pa_g) / to_abs(self.suction_pa_g)


class MarchResult(_Frozen):
    chainage_m: tuple[float, ...]
    elevation_m: tuple[float, ...]
    pressure_pa_g: tuple[float, ...]
    """Pressure leaving each point (station discharge where a station is)."""
    stations: tuple[Station, ...]
    delivery_pressure_pa_g: float

    @property
    def total_power_w(self) -> float:
        return sum(s.power_w for s in self.stations)

    @property
    def max_pressure_pa_g(self) -> float:
        return max(self.pressure_pa_g)


def march(
    profile: Profile, step: Step, p0_g: float, boosts: dict[int, float] | None = None
) -> list[float]:
    """Gauge pressures leaving each point; ``boosts`` sets discharge at station indices."""
    boosts = boosts or {}
    p = to_abs(boosts.get(0, p0_g))
    out = [to_gauge(p)]
    for k in range(len(profile.chainage_m) - 1):
        p = step(p, k)
        if k + 1 in boosts and not math.isnan(p):
            p = to_abs(boosts[k + 1])
        out.append(to_gauge(p) if not math.isnan(p) else math.nan)
    return out


def _below(p: float, limit: float) -> bool:
    return math.isnan(p) or p < limit - 1e-6


def plan_stations(
    profile: Profile,
    step: Step,
    *,
    supply_pa_g: float,
    delivery_min_pa_g: float,
    maop_pa_g: Sequence[float],
    min_line_pa_g: Sequence[float],
    min_suction_pa_g: float,
    discharge_cap_pa_g: float,
    power: PowerFn,
    max_ratio: float | None = None,
) -> MarchResult:
    n = len(profile.chainage_m)

    def cap(j: float, suction_g: float) -> float:
        c = min(discharge_cap_pa_g, maop_pa_g[int(j)])
        if max_ratio is not None:
            c = min(c, to_gauge(max_ratio * to_abs(suction_g)))
        return max(c, suction_g)

    # ---- 1. greedy siting at full discharge
    sites: list[tuple[int, float]] = [(0, supply_pa_g)]  # (index, suction)
    i, suction = 0, supply_pa_g
    while True:
        p = to_abs(cap(i, suction))
        candidate: tuple[int, float] | None = None
        restart = False
        for k in range(i, n - 1):
            p_next = step(p, k)
            last = k + 1 == n - 1
            limit = max(min_line_pa_g[k + 1], delivery_min_pa_g if last else -math.inf)
            if _below(to_gauge(p_next) if not math.isnan(p_next) else math.nan, limit):
                if candidate is None:
                    where = profile.chainage_m[k + 1]
                    raise HydraulicsError(
                        f"cannot reach chainage {where:.0f} m from the station at "
                        f"{profile.chainage_m[i]:.0f} m even at maximum discharge"
                    )
                i, suction = candidate
                sites.append(candidate)
                restart = True
                break
            p = p_next
            if not last and to_gauge(p) >= min_suction_pa_g - 1e-6:
                candidate = (k + 1, to_gauge(p))
        if not restart:
            break

    # ---- 2. trim discharges. Backward pass: the lowest discharge each leg needs
    # and hence the lowest suction its station can accept (the compression-ratio
    # limit couples the two). Forward pass: actual suctions and discharges.
    legs = [
        (start, sites[i + 1][0] if i + 1 < len(sites) else n - 1)
        for i, (start, _) in enumerate(sites)
    ]

    def feasible(d_g: float, start: int, end: int, end_min: float) -> bool:
        p = to_abs(d_g)
        for k in range(start, end):
            p = step(p, k)
            lim = end_min if k + 1 == end else min_line_pa_g[k + 1]
            if _below(to_gauge(p) if not math.isnan(p) else math.nan, lim):
                return False
        return True

    need_discharge = [0.0] * len(legs)
    end_min = delivery_min_pa_g
    for i in range(len(legs) - 1, -1, -1):
        start, end = legs[i]
        lo, hi = to_gauge(1.0), min(discharge_cap_pa_g, maop_pa_g[start])
        if feasible(lo, start, end, end_min):
            d = lo
        else:
            for _ in range(80):
                mid = (lo + hi) / 2
                lo, hi = (lo, mid) if feasible(mid, start, end, end_min) else (mid, hi)
            d = hi
        need_discharge[i] = d
        need_suction = d if max_ratio is None else to_gauge(to_abs(d) / max_ratio)
        end_min = max(min_suction_pa_g, need_suction)

    boosts: dict[int, float] = {}
    stations: list[Station] = []
    suction = supply_pa_g
    for (start, end), d in zip(legs, need_discharge, strict=True):
        discharge = max(d, suction)
        if discharge > cap(start, suction) + 1:
            raise HydraulicsError(  # pragma: no cover - siting guarantees feasibility
                f"station at {profile.chainage_m[start]:.0f} m cannot reach the required discharge"
            )
        boosts[start] = discharge
        pw = power(to_abs(suction), to_abs(discharge)) if discharge > suction + 1 else 0.0
        if pw > 0:
            stations.append(
                Station(
                    index=start,
                    chainage_m=profile.chainage_m[start],
                    suction_pa_g=suction,
                    discharge_pa_g=discharge,
                    power_w=pw,
                )
            )
        p = to_abs(discharge)
        for k in range(start, end):
            p = step(p, k)
        suction = to_gauge(p)

    pressures = march(profile, step, supply_pa_g, boosts)
    return MarchResult(
        chainage_m=profile.chainage_m,
        elevation_m=profile.elevation_m,
        pressure_pa_g=tuple(pressures),
        stations=tuple(stations),
        delivery_pressure_pa_g=pressures[-1],
    )


def envelope_checks(
    result: MarchResult,
    maop_pa_g: Sequence[float],
    min_line_pa_g: Sequence[float],
    delivery_min_pa_g: float,
    ref: str,
) -> tuple[CheckResult, ...]:
    """MAOP envelope, minimum line pressure and delivery pressure checks."""
    ur_max = max(p / m for p, m in zip(result.pressure_pa_g, maop_pa_g, strict=True))
    # ratio of required to actual absolute pressure, worst point (<= 1 passes)
    ur_min = max(
        to_abs(lim) / to_abs(p) if not math.isnan(p) else math.inf
        for p, lim in zip(result.pressure_pa_g, min_line_pa_g, strict=True)
    )
    delivered = result.delivery_pressure_pa_g
    return (
        CheckResult(
            name="pressure within MAOP envelope", demand=ur_max, capacity=1.0, unit="-", ref=ref
        ),
        CheckResult(
            name="minimum line pressure (abs ratio)", demand=ur_min, capacity=1.0, unit="-", ref=ref
        ),
        CheckResult(
            name="delivery pressure (abs)",
            demand=to_abs(delivery_min_pa_g),
            capacity=to_abs(delivered) if not math.isnan(delivered) else 1e-9,
            unit="Pa",
            ref="contract delivery pressure",
        ),
    )
