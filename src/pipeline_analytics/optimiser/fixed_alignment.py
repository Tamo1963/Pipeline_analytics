"""Fixed-alignment studies: build an option from a leg-by-leg route description.

Use this when the route is already chosen (e.g. through named towns) and no GIS
layers are available. Each leg gives its length, crossings, terrain shares and
towns. Towns become Class 3 / Class 2 bands (gas), and leg ends can be
offtakes. The result feeds the optimiser exactly like a routed corridor.

Every quantity is an *input assumption*; studies should record its source.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Literal

from pydantic import Field, model_validator

from pipeline_analytics.codes.class_location import ClassSegment
from pipeline_analytics.core.jurisdiction import Service
from pipeline_analytics.cost.boq import RouteQuantities
from pipeline_analytics.cost.rates import CrossingType, LandUse, Terrain, _Model
from pipeline_analytics.economics.option import OptionInput
from pipeline_analytics.hydraulics.profile import Offtake, Profile
from pipeline_analytics.mechanical.pressure import LocationClass

if TYPE_CHECKING:
    from pipeline_analytics.routing.geohazards import HazardCrossing

_RANK = {
    LocationClass.CLASS_1_DIV_2: 1,
    LocationClass.CLASS_2: 2,
    LocationClass.CLASS_3: 3,
    LocationClass.CLASS_4: 4,
}
_BY_RANK = {v: k for k, v in _RANK.items()}


class Town(_Model):
    name: str
    at_km: float = Field(ge=0, description="position within the leg")
    class3_km: float = Field(default=0.0, ge=0, description="Class 3 length centred on the town")
    class2_km: float = Field(default=0.0, ge=0, description="Class 2 length each side of Class 3")


class LegHazard(_Model):
    """A mapped fault or ground-movement zone on a leg (no GIS geometry needed)."""

    name: str
    kind: Literal["fault", "landslide", "lateral_spread", "subsidence"]
    at_km: float = Field(ge=0, description="position within the leg (zone centre)")
    displacement_m: float = Field(gt=0, description="fault slip or ground displacement")
    crossing_angle_deg: float = Field(
        default=90.0, ge=0, lt=360,
        description="fault: pipe direction to fault strike (counter-clockwise, Aki-Richards "
        "strike); zone: pipe direction to the movement direction",
    )  # fmt: skip
    dip_deg: float = Field(default=90.0, gt=0, le=90)
    rake_deg: float = Field(default=0.0, ge=-180, le=180)
    width_m: float = Field(default=50.0, gt=0, description="zone length along the pipe")
    pattern: Literal["cosine", "block"] = "cosine"


class Leg(_Model):
    name: str
    length_km: float = Field(gt=0)
    hdd_m: float = Field(default=0.0, ge=0, description="wide-river HDD (or trenched) length")
    narrow_river_crossings: int = Field(default=0, ge=0, description="open-cut khals/streams")
    national_highways: int = Field(default=0, ge=0)
    state_highways: int = Field(default=0, ge=0)
    minor_roads: int = Field(default=0, ge=0)
    railways: int = Field(default=0, ge=0)
    marsh_fraction: float = Field(default=0.0, ge=0, le=1, description="wetland / flood plain")
    urban_fraction: float = Field(default=0.0, ge=0, le=1)
    forest_fraction: float = Field(default=0.0, ge=0, le=1)
    wasteland_fraction: float = Field(default=0.0, ge=0, le=1)
    elevation_m: float = 0.0
    towns: tuple[Town, ...] = ()
    geohazards: tuple[LegHazard, ...] = ()
    end_offtake_share: float = Field(default=0.0, ge=0, lt=1, description="of the inlet flow")
    end_offtake_min_pa_g: float = 0.0
    end_name: str = ""

    @model_validator(mode="after")
    def _check(self) -> Leg:
        if self.marsh_fraction + self.urban_fraction + self.forest_fraction > 1 + 1e-9:
            raise ValueError(f"{self.name}: terrain fractions exceed 1")
        if self.hdd_m > self.length_km * 1000:
            raise ValueError(f"{self.name}: HDD length exceeds the leg")
        return self


class Alignment(_Model):
    name: str
    legs: tuple[Leg, ...] = Field(min_length=1)
    narrow_river_width_m: float = 30.0
    extra_terminals: int = Field(default=0, ge=0, description="e.g. a processing unit at the inlet")

    @property
    def length_m(self) -> float:
        return sum(leg.length_km for leg in self.legs) * 1000

    def leg_starts_m(self) -> list[float]:
        out, x = [], 0.0
        for leg in self.legs:
            out.append(x)
            x += leg.length_km * 1000
        return out

    def profile(self, step_m: float = 1000.0) -> Profile:
        """Piecewise-flat profile at each leg's elevation (deltas, plains)."""
        pts: dict[float, float] = {}
        for start, leg in zip(self.leg_starts_m(), self.legs, strict=True):
            n = max(1, round(leg.length_km * 1000 / step_m))
            for i in range(n + 1):
                pts[round(start + leg.length_km * 1000 * i / n, 6)] = leg.elevation_m
        xs = sorted(pts)
        return Profile(chainage_m=tuple(xs), elevation_m=tuple(pts[x] for x in xs))

    def class_segments(self, step_m: float = 50.0) -> tuple[ClassSegment, ...]:
        """Location classes from the town bands (highest class wins), merged."""
        length = self.length_m
        n = int(length // step_m) + 1
        xs = [min(i * step_m, length) for i in range(n)]
        if xs[-1] < length:
            xs.append(length)
        rank = [1] * len(xs)
        why = ["rural"] * len(xs)
        for start, leg in zip(self.leg_starts_m(), self.legs, strict=True):
            for t in leg.towns:
                c = start + t.at_km * 1000
                h3, h2 = t.class3_km * 500, t.class3_km * 500 + t.class2_km * 1000
                for i, x in enumerate(xs):
                    if abs(x - c) <= h3 and rank[i] < 3:
                        rank[i], why[i] = 3, t.name
                    elif abs(x - c) <= h2 and rank[i] < 2:
                        rank[i], why[i] = 2, f"{t.name} approach"
        segs: list[ClassSegment] = []
        s0 = 0
        for i in range(1, len(xs) + 1):
            if i == len(xs) or rank[i] != rank[s0]:
                end = xs[i] if i < len(xs) else xs[-1]
                if end > xs[s0]:
                    segs.append(
                        ClassSegment(
                            start_m=xs[s0], end_m=end, location_class=_BY_RANK[rank[s0]],
                            reason=why[s0],
                        )
                    )  # fmt: skip
                s0 = i
        return tuple(segs)

    def location_class_at(self, chainage_m: float) -> LocationClass:
        for seg in self.class_segments():
            if seg.start_m - 1e-6 <= chainage_m <= seg.end_m + 1e-6:
                return seg.location_class
        return LocationClass.CLASS_1_DIV_2

    def geohazard_crossings(self) -> tuple[HazardCrossing, ...]:
        """Leg hazards as crossings for ``routing.geohazards.assess_geohazards``."""
        from pipeline_analytics.mechanical.pgd import GroundMovement, fault_movement, settlement
        from pipeline_analytics.routing.geohazards import HazardCrossing

        out = []
        for start, leg in zip(self.leg_starts_m(), self.legs, strict=True):
            for h in leg.geohazards:
                ch = start + h.at_km * 1000
                if h.kind == "fault":
                    mv = fault_movement(h.displacement_m, h.crossing_angle_deg,
                                        dip_deg=h.dip_deg, rake_deg=h.rake_deg)  # fmt: skip
                    out.append(HazardCrossing(
                        hazard=h.name, kind="fault", chainage_m=ch, point=(ch, 0.0),
                        crossing_angle_deg=h.crossing_angle_deg, movement=mv,
                        slip_m=h.displacement_m, dip_deg=h.dip_deg, rake_deg=h.rake_deg,
                    ))  # fmt: skip
                    continue
                if h.kind == "subsidence":
                    mv = settlement(h.displacement_m, h.width_m)
                else:
                    a = math.radians(h.crossing_angle_deg)
                    mv = GroundMovement(
                        kind=h.pattern, width_m=h.width_m,
                        axial_m=h.displacement_m * math.cos(a),
                        transverse_m=h.displacement_m * math.sin(a),
                        label=f"{h.kind} {h.displacement_m:g} m over {h.width_m:g} m",
                    )  # fmt: skip
                out.append(HazardCrossing(
                    hazard=h.name, kind=h.kind, chainage_m=ch, point=(ch, 0.0),
                    crossing_angle_deg=h.crossing_angle_deg, width_m=h.width_m, movement=mv,
                ))  # fmt: skip
        return tuple(sorted(out, key=lambda c: c.chainage_m))

    def offtakes(self) -> tuple[Offtake, ...]:
        out = []
        for start, leg in zip(self.leg_starts_m(), self.legs, strict=True):
            end = start + leg.length_km * 1000
            if leg.end_offtake_share and end < self.length_m:
                out.append(
                    Offtake(
                        chainage_m=end,
                        share=leg.end_offtake_share,
                        min_pressure_pa_g=leg.end_offtake_min_pa_g,
                        name=leg.end_name,
                    )
                )
        return tuple(out)

    def route_quantities(self) -> RouteQuantities:
        terrain: dict[Terrain, float] = {}
        land: dict[LandUse, float] = {}
        crossings: dict[CrossingType, int] = {}
        hdd = 0.0
        open_cut = 0
        for leg in self.legs:
            dry = leg.length_km * 1000 - leg.hdd_m
            for t, f in ((Terrain.MARSH, leg.marsh_fraction), (Terrain.URBAN, leg.urban_fraction),
                         (Terrain.FOREST, leg.forest_fraction)):  # fmt: skip
                if f:
                    terrain[t] = terrain.get(t, 0.0) + f * dry
            land_shares = (
                (LandUse.URBAN, leg.urban_fraction),
                (LandUse.FOREST, leg.forest_fraction),
                (LandUse.WASTELAND, leg.wasteland_fraction),
            )
            for lu, f in land_shares:
                if f:
                    land[lu] = land.get(lu, 0.0) + f * dry
            for ct, n in ((CrossingType.NATIONAL_HIGHWAY, leg.national_highways),
                          (CrossingType.STATE_HIGHWAY, leg.state_highways),
                          (CrossingType.MINOR_ROAD, leg.minor_roads),
                          (CrossingType.RAILWAY, leg.railways)):  # fmt: skip
                if n:
                    crossings[ct] = crossings.get(ct, 0) + n
            hdd += leg.hdd_m
            open_cut += leg.narrow_river_crossings
        return RouteQuantities(
            terrain_m=terrain,
            land_use_m=land,
            crossings=crossings,
            hdd_m=hdd,
            open_cut_river_m=open_cut * self.narrow_river_width_m,
            terminals=2 + len(self.offtakes()) + self.extra_terminals,
        )

    @property
    def wet_length_m(self) -> float:
        return sum(leg.marsh_fraction * (leg.length_km * 1000 - leg.hdd_m) for leg in self.legs)

    def option_input(self, base: OptionInput, profile_step_m: float = 1000.0) -> OptionInput:
        """``base`` with this alignment's profile, classes, quantities and offtakes."""
        data = base.model_dump()
        data.update(
            profile=self.profile(profile_step_m).model_dump(),
            class_segments=(
                [s.model_dump() for s in self.class_segments()]
                if base.service is Service.GAS
                else []
            ),
            route=self.route_quantities().model_dump(),
            wet_length_m=self.wet_length_m,
            offtakes=[o.model_dump() for o in self.offtakes()],
        )
        return OptionInput.model_validate(data)
