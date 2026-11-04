"""Location class along a gas pipeline route (ASME B31.8 sec. 840.2).

PNGRB T4S and OISD-STD-226 use the same scheme; verify the current text before
issuing a design.

* Class location unit: any continuous 1.6 km (1 mile) of pipeline, 200 m (220 yd)
  either side of the centreline. The unit slides along the route.
* Count buildings for human occupancy in the unit (each dwelling unit in a
  multi-dwelling building counts separately):
  - Class 1: 10 or fewer
  - Class 2: 11 to 45
  - Class 3: 46 or more
  - Class 4: buildings with four or more storeys are prevalent
* Class 3 also applies within 91 m (100 yd) of a building or small, well-defined
  outdoor place occupied by 20 or more people (on at least 5 days a week for
  10 weeks in any 12 months).
* Boundaries (cluster rule): where a cluster of buildings makes a unit Class 2
  or 3, the class may end 200 m (660 ft) from the nearest building of the
  cluster. Class 4 ends 200 m from the nearest four-storey building.

Every point gets the highest class of any unit covering it. Liquid pipelines
(ASME B31.4 / OISD-STD-141) have no location classes.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Iterable, Sequence

from pydantic import Field

from pipeline_analytics.core.jurisdiction import Jurisdiction, Service
from pipeline_analytics.mechanical.common import PipeSection, _Frozen
from pipeline_analytics.mechanical.pressure import LocationClass, pressure_design

UNIT_LENGTH_M = 1609.344  # 1 mile
UNIT_HALF_WIDTH_M = 201.168  # 220 yd
CLUSTER_BOUNDARY_M = 201.168  # 660 ft
ASSEMBLY_DISTANCE_M = 91.44  # 100 yd
ASSEMBLY_MIN_OCCUPANCY = 20
CLASS4_MIN_STOREYS = 4

_RANK = {
    LocationClass.CLASS_1_DIV_2: 1,
    LocationClass.CLASS_2: 2,
    LocationClass.CLASS_3: 3,
    LocationClass.CLASS_4: 4,
}
_BY_RANK = {v: k for k, v in _RANK.items()}


class Building(_Frozen):
    """A building near the route, located by chainage and offset (m)."""

    chainage_m: float
    offset_m: float
    dwelling_units: int = Field(default=1, ge=0)
    storeys: int = Field(default=1, ge=1)


class AssemblyPlace(_Frozen):
    """A building or small outdoor area regularly occupied by a group of people."""

    chainage_m: float
    offset_m: float
    occupancy: int = Field(ge=0)
    name: str = ""


class ClassSegment(_Frozen):
    start_m: float
    end_m: float
    location_class: LocationClass
    reason: str

    @property
    def length_m(self) -> float:
        return self.end_m - self.start_m


def _unit_class(units: int) -> int:
    return 3 if units >= 46 else 2 if units >= 11 else 1


def classify_route(
    route_length_m: float,
    buildings: Iterable[Building] = (),
    assembly_places: Iterable[AssemblyPlace] = (),
    *,
    step_m: float = 10.0,
    cluster_rule: bool = True,
) -> list[ClassSegment]:
    """Location-class segments along a route of length ``route_length_m``.

    Evaluated on a grid of ``step_m``; adjacent points of the same class are
    merged. With ``cluster_rule`` a Class 2/3 unit only raises the class within
    200 m of its buildings, otherwise across the whole 1.6 km unit.
    """
    if route_length_m <= 0 or step_m <= 0:
        raise ValueError("route length and step must be positive")
    near = sorted(
        (b for b in buildings if abs(b.offset_m) <= UNIT_HALF_WIDTH_M and b.dwelling_units),
        key=lambda b: b.chainage_m,
    )
    n = int(route_length_m // step_m) + 1
    xs = [min(i * step_m, route_length_m) for i in range(n)]
    if xs[-1] < route_length_m:
        xs.append(route_length_m)
    rank = [1] * len(xs)
    why = ["fewer than 11 dwelling units per 1.6 km"] * len(xs)

    def raise_to(lo: float, hi: float, r: int, reason: str) -> None:
        for i, x in enumerate(xs):
            if lo - 1e-9 <= x <= hi + 1e-9 and r > rank[i]:
                rank[i], why[i] = r, reason

    # Class 2/3 from sliding 1.6 km units. A unit must lie on the pipeline, so
    # starts run from 0 to (length - 1.6 km). Counts only change when a building
    # enters or leaves, so starts at building chainages (left- and right-aligned)
    # plus the grid find every distinct count.
    chain = [b.chainage_m for b in near]
    prefix = [0]
    for b in near:
        prefix.append(prefix[-1] + b.dwelling_units)
    last_start = max(0.0, route_length_m - UNIT_LENGTH_M)
    starts = {x for x in xs if x <= last_start}
    starts |= {min(max(c, 0.0), last_start) for c in chain}
    starts |= {min(max(c - UNIT_LENGTH_M, 0.0), last_start) for c in chain}
    intervals: dict[int, list[tuple[float, float, str]]] = {2: [], 3: []}
    for s0 in sorted(starts):
        i0 = bisect_left(chain, s0 - 1e-9)
        i1 = bisect_right(chain, s0 + UNIT_LENGTH_M + 1e-9)
        units = prefix[i1] - prefix[i0]
        r = _unit_class(units)
        if r == 1:
            continue
        if cluster_rule:
            lo, hi = chain[i0] - CLUSTER_BOUNDARY_M, chain[i1 - 1] + CLUSTER_BOUNDARY_M
        else:
            lo, hi = s0, s0 + UNIT_LENGTH_M
        intervals[r].append((lo, hi, f"{units} dwelling units in a 1.6 km unit"))
    for r in (2, 3):
        for lo, hi, reason in _union(intervals[r]):
            raise_to(lo, hi, r, reason)

    for b in near:
        if b.storeys >= CLASS4_MIN_STOREYS:
            raise_to(
                b.chainage_m - CLUSTER_BOUNDARY_M,
                b.chainage_m + CLUSTER_BOUNDARY_M,
                4,
                f"building of {b.storeys} storeys",
            )

    for a in assembly_places:
        if a.occupancy < ASSEMBLY_MIN_OCCUPANCY or abs(a.offset_m) > ASSEMBLY_DISTANCE_M:
            continue
        half = (ASSEMBLY_DISTANCE_M**2 - a.offset_m**2) ** 0.5
        label = a.name or "place of assembly"
        raise_to(a.chainage_m - half, a.chainage_m + half, 3, f"{label} ({a.occupancy} people)")

    segments: list[ClassSegment] = []
    start = 0
    for i in range(1, len(xs) + 1):
        if i == len(xs) or rank[i] != rank[start] or why[i] != why[start]:
            end = xs[i] if i < len(xs) else xs[-1]
            segments.append(
                ClassSegment(
                    start_m=xs[start],
                    end_m=end,
                    location_class=_BY_RANK[rank[start]],
                    reason=why[start],
                )
            )
            start = i
    return _merge(segments)


def _union(items: list[tuple[float, float, str]]) -> list[tuple[float, float, str]]:
    """Union of overlapping intervals; keeps the reason with the highest count."""
    out: list[tuple[float, float, str]] = []
    for lo, hi, why in sorted(items):
        if out and lo <= out[-1][1]:
            plo, phi, pwhy = out[-1]
            best = max(pwhy, why, key=lambda w: int(w.split()[0]))
            out[-1] = (plo, max(phi, hi), best)
        else:
            out.append((lo, hi, why))
    return out


def _merge(segments: list[ClassSegment]) -> list[ClassSegment]:
    """Merge neighbours of the same class (keeping the first reason)."""
    out: list[ClassSegment] = []
    for s in segments:
        if out and out[-1].location_class is s.location_class:
            out[-1] = out[-1].model_copy(update={"end_m": s.end_m})
        elif s.length_m > 0 or not out:
            out.append(s)
        else:
            out[-1] = out[-1].model_copy(update={"end_m": s.end_m})
    return out


def class_at(segments: Sequence[ClassSegment], chainage_m: float) -> LocationClass:
    for s in segments:
        if s.start_m - 1e-9 <= chainage_m <= s.end_m + 1e-9:
            return s.location_class
    raise ValueError(f"chainage {chainage_m} m is outside the classified route")


def class_summary(segments: Sequence[ClassSegment]) -> dict[LocationClass, float]:
    """Total length (m) in each location class."""
    totals: dict[LocationClass, float] = {}
    for s in segments:
        totals[s.location_class] = totals.get(s.location_class, 0.0) + s.length_m
    return totals


def maop_along_route(
    segments: Sequence[ClassSegment],
    pipe: PipeSection,
    chainages_m: Sequence[float],
    *,
    service: Service = Service.GAS,
    joint_factor: float = 1.0,
    design_temp_c: float = 50.0,
    allowance_m: float = 0.0,
    jurisdiction: Jurisdiction = Jurisdiction.INDIA,
) -> tuple[float, ...]:
    """MAOP (Pa, gauge) at each chainage for a single wall/grade.

    Shows where a uniform pipe is limited by location class, e.g. to feed the
    hydraulic MAOP envelope or to decide where heavier wall is needed.
    """
    cache: dict[LocationClass, float] = {}
    out = []
    for x in chainages_m:
        lc = class_at(segments, x) if service is Service.GAS else LocationClass.CLASS_1_DIV_2
        if lc not in cache:
            cache[lc] = pressure_design(
                pipe,
                0.0,
                service,
                lc,
                joint_factor=joint_factor,
                design_temp_c=design_temp_c,
                allowance=allowance_m,
                jurisdiction=jurisdiction,
            ).maop_pa
        out.append(cache[lc])
    return tuple(out)
