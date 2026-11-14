"""Bill of quantities (BOQ) and CAPEX estimate.

Indirect costs are built up in this order (D = direct cost excluding land,
L = land):

    taxes        = t x D
    engineering  = e x D
    owner's cost = o x (D + L)
    contingency  = c x (D + taxes + L + engineering + owner's cost)

Accuracy with placeholder rates is AACE Class 5 (screening). Every estimate
records whether placeholder rates were used.
"""

from __future__ import annotations

import math
from collections.abc import Iterable

from pydantic import Field

from pipeline_analytics.core.jurisdiction import Service
from pipeline_analytics.cost.rates import CrossingType, LandUse, Terrain, UnitRates, _Model
from pipeline_analytics.mechanical.pressure import LocationClass

# ASME B31.8 846.1.1 maximum sectionalising-valve spacing by location class
# (20 / 15 / 10 / 5 miles). Liquid default is a typical rural spacing; confirm
# against OISD-STD-141 / PNGRB T4S for the project.
SV_SPACING_M = {
    LocationClass.CLASS_1_DIV_1: 32_187.0,
    LocationClass.CLASS_1_DIV_2: 32_187.0,
    LocationClass.CLASS_2: 24_140.0,
    LocationClass.CLASS_3: 16_093.0,
    LocationClass.CLASS_4: 8_047.0,
}
LIQUID_SV_SPACING_M = 30_000.0


class PipeSegmentQty(_Model):
    """A length of uniform pipe."""

    length_m: float = Field(gt=0)
    od_m: float = Field(gt=0)
    wt_m: float = Field(gt=0)
    grade: str
    process: str
    location_class: LocationClass = LocationClass.CLASS_1_DIV_2

    @property
    def tonnes(self) -> float:
        return math.pi * 7850.0 * (self.od_m - self.wt_m) * self.wt_m * self.length_m / 1000


class RouteQuantities(_Model):
    """Route-dependent quantities (from the alignment / GIS in Phase 4)."""

    terrain_m: dict[Terrain, float] = Field(default_factory=dict)
    land_use_m: dict[LandUse, float] = Field(default_factory=dict)
    crossings: dict[CrossingType, int] = Field(default_factory=dict)
    hdd_m: float = Field(default=0.0, ge=0, description="total HDD crossing length")
    open_cut_river_m: float = Field(default=0.0, ge=0)
    concrete_coating_m3: float = Field(default=0.0, ge=0)
    terminals: int = Field(default=2, ge=0)
    scraper_stations: int = Field(default=0, ge=0, description="intermediate pigging stations")


class StationQty(_Model):
    required_kw: float = Field(ge=0)
    count: int = Field(ge=0)


class LineItem(_Model):
    group: str
    description: str
    quantity: float
    unit: str
    rate: float

    @property
    def amount(self) -> float:
        return self.quantity * self.rate


class CapexEstimate(_Model):
    items: tuple[LineItem, ...]
    currency: str
    base_year: int
    placeholder_rates: bool
    taxes: float
    engineering: float
    owners_cost: float
    contingency: float

    @property
    def direct_excl_land(self) -> float:
        return sum(i.amount for i in self.items if i.group != "land")

    @property
    def land(self) -> float:
        return sum(i.amount for i in self.items if i.group == "land")

    @property
    def total(self) -> float:
        return (
            self.direct_excl_land
            + self.land
            + self.taxes
            + self.engineering
            + self.owners_cost
            + self.contingency
        )

    @property
    def total_factor(self) -> float:
        """Total / direct-excluding-land: applied to later (sustaining) direct costs."""
        d = self.direct_excl_land
        return self.total / d if d else 1.0

    def by_group(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for i in self.items:
            out[i.group] = out.get(i.group, 0.0) + i.amount
        for name in ("taxes", "engineering", "owners_cost", "contingency"):
            out[name] = getattr(self, name)
        return out

    @property
    def accuracy_class(self) -> str:
        return "AACE Class 5 (placeholder rates)" if self.placeholder_rates else "per rate source"


def with_indirects(direct_excl_land: float, land: float, rates: UnitRates) -> float:
    """Direct cost plus indirects, built up exactly as in :func:`capex_estimate`.

    ``(D (1 + t + e + o) + L (1 + o)) (1 + c)``
    """
    i = rates.indirects
    return (
        direct_excl_land * (1 + i.taxes + i.engineering_pmc + i.owners_cost)
        + land * (1 + i.owners_cost)
    ) * (1 + i.contingency)


def station_items(service: Service, st: StationQty, rates: UnitRates) -> list[LineItem]:
    """Compressor / pump station cost: fixed per station + per installed kW."""
    s = rates.stations
    if st.count == 0:
        return []
    kind, fixed, per_kw = (
        ("compressor", s.compressor_fixed, s.compressor_per_kw)
        if service is Service.GAS
        else ("pump", s.pump_fixed, s.pump_per_kw)
    )
    return [
        LineItem(
            group="stations",
            description=f"{kind} stations (fixed)",
            quantity=st.count,
            unit="nos",
            rate=fixed,
        ),
        LineItem(
            group="stations",
            description=f"{kind} power installed",
            unit="kW",
            quantity=st.required_kw * s.installed_power_factor,
            rate=per_kw,
        ),
        LineItem(
            group="land",
            description=f"{kind} station land",
            unit="ha",
            quantity=st.count * rates.land.station_land_ha,
            rate=rates.land.permanent_land_per_ha,
        ),
    ]


def sectionalising_valves(service: Service, segments: Iterable[PipeSegmentQty]) -> int:
    """Number of mainline sectionalising-valve stations (excluding terminals)."""
    segs = list(segments)
    if service is Service.LIQUID:
        need = sum(s.length_m for s in segs) / LIQUID_SV_SPACING_M
    else:
        need = sum(s.length_m / SV_SPACING_M[s.location_class] for s in segs)
    return max(0, math.ceil(need - 1e-9) - 1)


def capex_estimate(
    service: Service,
    segments: Iterable[PipeSegmentQty],
    route: RouteQuantities,
    stations: StationQty,
    rates: UnitRates,
    coating: str = "3LPE",
) -> CapexEstimate:
    """CAPEX from pipe segments, route quantities and stations."""
    segs = list(segments)
    if not segs:
        raise ValueError("at least one pipe segment is needed")
    length = sum(s.length_m for s in segs)
    od_in = max(s.od_m for s in segs) / 0.0254
    items: list[LineItem] = []

    # line pipe, by grade/process
    for s in segs:
        items.append(
            LineItem(
                group="line pipe",
                description=f"{s.grade} {s.process} {s.od_m * 1000:.0f}x{s.wt_m * 1000:.2f} mm",
                quantity=s.tonnes,
                unit="t",
                rate=rates.pipe_rate_per_tonne(s.grade, s.process),
            )
        )
    area = sum(math.pi * s.od_m * s.length_m for s in segs)
    items.append(
        LineItem(
            group="coating",
            description=f"{coating} external coating",
            quantity=area,
            unit="m2",
            rate=rates.coating_per_m2[coating],
        )
    )
    if route.concrete_coating_m3:
        items.append(
            LineItem(
                group="coating",
                description="concrete weight coating",
                unit="m3",
                quantity=route.concrete_coating_m3,
                rate=rates.concrete_coating_per_m3,
            )
        )

    # Construction by terrain. Unspecified length is flat, except HDD lengths:
    # those are priced as HDD only (no open-cut laying, no right of use).
    conventional = length - route.hdd_m
    if conventional < -1e-6:
        raise ValueError("HDD length exceeds the pipeline length")
    terrain = dict(route.terrain_m)
    if sum(terrain.values()) > conventional + 1e-6:
        raise ValueError("terrain lengths exceed the pipeline length less HDD")
    terrain[Terrain.FLAT] = terrain.get(Terrain.FLAT, 0.0) + max(
        0.0, conventional - sum(terrain.values())
    )
    for t, m in terrain.items():
        if m > 0:
            items.append(
                LineItem(
                    group="construction",
                    description=f"laying, {t} terrain",
                    quantity=od_in * m / 1000,
                    unit="inch-km",
                    rate=rates.laying_per_inch_km * rates.terrain_factor[t],
                )
            )

    cr = rates.crossings
    if route.hdd_m:
        items.append(
            LineItem(
                group="crossings",
                description="HDD crossings",
                quantity=od_in * route.hdd_m,
                unit="inch-m",
                rate=cr.hdd_per_inch_m,
            )
        )
    if route.open_cut_river_m:
        items.append(
            LineItem(
                group="crossings",
                description="open-cut river crossings",
                quantity=od_in * route.open_cut_river_m,
                unit="inch-m",
                rate=cr.open_cut_river_per_inch_m,
            )
        )
    for ct, n in route.crossings.items():
        if n:
            items.append(
                LineItem(
                    group="crossings",
                    description=f"{ct} crossings",
                    quantity=n,
                    unit="nos",
                    rate=cr.per_crossing[ct],
                )
            )

    items.extend(station_items(service, stations, rates))
    st = rates.stations
    n_sv = sectionalising_valves(service, segs)
    for desc, n, rate in (
        ("terminals (dispatch/receipt)", route.terminals, st.terminal_each),
        ("sectionalising valve stations", n_sv, st.sectionalising_valve_each),
        ("intermediate scraper stations", route.scraper_stations, st.scraper_station_each),
    ):
        if n:
            items.append(
                LineItem(group="facilities", description=desc, quantity=n, unit="nos", rate=rate)
            )
    km = length / 1000
    items.append(
        LineItem(
            group="systems",
            description="cathodic protection",
            quantity=km,
            unit="km",
            rate=rates.per_km.cathodic_protection,
        )
    )
    items.append(
        LineItem(
            group="systems",
            description="SCADA, telecom (OFC), leak detection",
            quantity=km,
            unit="km",
            rate=rates.per_km.scada_telecom,
        )
    )

    # right of use: ROW width x length by land use (unspecified = agricultural)
    land = dict(route.land_use_m)
    if sum(land.values()) > conventional + 1e-6:
        raise ValueError("land-use lengths exceed the pipeline length less HDD")
    land[LandUse.AGRICULTURAL] = land.get(LandUse.AGRICULTURAL, 0.0) + max(
        0.0, conventional - sum(land.values())
    )
    for lu, m in land.items():
        if m > 0:
            items.append(
                LineItem(
                    group="land",
                    description=f"right of use, {lu}",
                    quantity=m * rates.land.row_width_m / 10_000,
                    unit="ha",
                    rate=rates.land.rou_compensation_per_ha[lu],
                )
            )

    d = sum(i.amount for i in items if i.group != "land")
    lnd = sum(i.amount for i in items if i.group == "land")
    ind = rates.indirects
    taxes, eng, own = ind.taxes * d, ind.engineering_pmc * d, ind.owners_cost * (d + lnd)
    cont = ind.contingency * (d + taxes + lnd + eng + own)
    return CapexEstimate(
        items=tuple(items),
        currency=rates.currency,
        base_year=rates.base_year,
        placeholder_rates=rates.placeholder,
        taxes=taxes,
        engineering=eng,
        owners_cost=own,
        contingency=cont,
    )
