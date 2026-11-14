"""Lateral spread displacement: Youd, Hansen & Bartlett (2002) MLR.

Youd T.L., Hansen C.M., Bartlett S.F. (2002), *Revised multilinear regression
equations for prediction of lateral spread displacement*, J. Geotech.
Geoenviron. Eng. 128(12), 1007-1017.

    log D_H = b0 + 1.532 M - 1.406 log R* - 0.012 R + b_g log G
              + 0.540 log T15 + 3.413 log(100 - F15) - 0.795 log(D50_15 + 0.1 mm)

* Free face (river bank, channel): b0 = -16.713, G = W = 100 H / L (%), b_g = 0.592.
* Ground slope: b0 = -16.213, G = S (%), b_g = 0.338.
* R* = R + R0, R0 = 10^(0.89 M - 5.64), with R the horizontal distance to the
  nearest seismic energy source (km).

D_H is in metres. Away from a free face it decays as W^0.592, so as
L^-0.592. The regression is empirical, with scatter of about a factor of 2.
Outside its data range (roughly M 6-8, W 1-20 %, S 0.1-6 %, T15 1-15 m,
F15 <= 50 %, D50_15 0.1-1 mm, and depth to liquefied soil under 10 m) the
result is flagged.
"""

from __future__ import annotations

import itertools
import math

from pydantic import BaseModel, ConfigDict

from pipeline_analytics.geotech.liquefaction import TriggeringResult

FREE_FACE_B0 = -16.713
GROUND_SLOPE_B0 = -16.213


class _M(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def youd2002(
    magnitude: float,
    distance_km: float,
    t15_m: float,
    f15_pct: float,
    d50_15_mm: float,
    *,
    free_face_ratio_pct: float | None = None,
    slope_pct: float | None = None,
) -> float:
    """Lateral spread displacement D_H (m). Give exactly one of W (free face) or S (slope)."""
    if (free_face_ratio_pct is None) == (slope_pct is None):
        raise ValueError("give either free_face_ratio_pct (W) or slope_pct (S)")
    if t15_m <= 0:
        return 0.0
    r0 = 10 ** (0.89 * magnitude - 5.64)
    r_star = distance_km + r0
    common = (1.532 * magnitude - 1.406 * math.log10(r_star) - 0.012 * distance_km
              + 0.540 * math.log10(t15_m) + 3.413 * math.log10(100 - f15_pct)
              - 0.795 * math.log10(d50_15_mm + 0.1))  # fmt: skip
    if free_face_ratio_pct is not None:
        log_dh = FREE_FACE_B0 + common + 0.592 * math.log10(free_face_ratio_pct)
    else:
        assert slope_pct is not None
        log_dh = GROUND_SLOPE_B0 + common + 0.338 * math.log10(slope_pct)
    return float(10**log_dh)


def range_warnings(
    magnitude: float, t15_m: float, f15_pct: float, d50_15_mm: float,
    free_face_ratio_pct: float | None = None, slope_pct: float | None = None,
) -> list[str]:  # fmt: skip
    w = []
    if not 6.0 <= magnitude <= 8.0:
        w.append(f"M {magnitude:g} outside about 6-8")
    if t15_m and not 1.0 <= t15_m <= 15.0:
        w.append(f"T15 {t15_m:.1f} m outside about 1-15 m")
    if f15_pct > 50:
        w.append(f"F15 {f15_pct:.0f} % above about 50 %")
    if t15_m and not 0.1 <= d50_15_mm <= 1.0:
        w.append(f"D50_15 {d50_15_mm:.2f} mm outside about 0.1-1 mm")
    if free_face_ratio_pct is not None and not 1.0 <= free_face_ratio_pct <= 20.0:
        w.append(f"W {free_face_ratio_pct:.1f} % outside about 1-20 %")
    if slope_pct is not None and not 0.1 <= slope_pct <= 6.0:
        w.append(f"S {slope_pct:.2f} % outside about 0.1-6 %")
    return w


class SpreadPoint(_M):
    distance_m: float
    displacement_m: float


class LateralSpread(_M):
    """Lateral spread behind a free face (or on a slope), from a triggering analysis."""

    mode: str
    magnitude: float
    distance_km: float
    t15_m: float
    f15_pct: float
    d50_15_mm: float
    free_face_height_m: float | None = None
    slope_pct: float | None = None
    profile: tuple[SpreadPoint, ...]
    """Displacement against distance from the toe of the free face (free-face mode)."""
    extent_m: float
    """Distance over which D_H >= ``threshold_m``."""
    threshold_m: float
    max_displacement_m: float
    warnings: tuple[str, ...]

    def displacement_at(self, distance_m: float) -> float:
        pts = self.profile
        if distance_m <= pts[0].distance_m:
            return pts[0].displacement_m
        for a, b in itertools.pairwise(pts):
            if distance_m <= b.distance_m:
                t = (distance_m - a.distance_m) / (b.distance_m - a.distance_m)
                return a.displacement_m + t * (b.displacement_m - a.displacement_m)
        return 0.0


def lateral_spread(
    trig: TriggeringResult,
    *,
    free_face_height_m: float | None = None,
    slope_pct: float | None = None,
    distance_km: float | None = None,
    threshold_m: float = 0.05,
    max_w_pct: float = 20.0,
) -> LateralSpread:
    """Lateral spread for a site from its triggering result.

    Free face: D_H at distance L from the toe uses W = 100 H / L, capped at
    ``max_w_pct`` (20 %, the upper end of the data) close to the face. The
    profile runs out to where D_H falls below ``threshold_m`` or W reaches 1 %.
    """
    eq = trig.earthquake
    r = eq.distance_km if distance_km is None else distance_km
    if r is None:
        raise ValueError("the lateral-spread model needs the source distance (km)")
    t15, f15, d50 = trig.youd_inputs()
    if t15 == 0:
        return LateralSpread(
            mode="none", magnitude=eq.magnitude, distance_km=r, t15_m=0, f15_pct=0,
            d50_15_mm=0, profile=(SpreadPoint(distance_m=0, displacement_m=0),), extent_m=0,
            threshold_m=threshold_m, max_displacement_m=0,
            warnings=("no liquefied layer with (N1)60 < 15: no lateral spread predicted",),
        )  # fmt: skip
    if free_face_height_m is not None:
        h = free_face_height_m
        pts = []
        l_min = 100 * h / max_w_pct
        for i in range(200):
            dist = l_min * (1.06**i)
            w = min(100 * h / dist, max_w_pct)
            if w < 1.0:
                break
            dh = youd2002(eq.magnitude, r, t15, f15, d50, free_face_ratio_pct=w)
            pts.append(SpreadPoint(distance_m=dist, displacement_m=dh))
            if dh < threshold_m:
                break
        pts.insert(0, SpreadPoint(distance_m=0.0, displacement_m=pts[0].displacement_m))
        # taper to zero beyond the last point instead of an abrupt step
        pts.append(SpreadPoint(distance_m=pts[-1].distance_m * 1.25, displacement_m=0.0))
        extent = max((p.distance_m for p in pts if p.displacement_m >= threshold_m), default=0.0)
        warn = range_warnings(eq.magnitude, t15, f15, d50, free_face_ratio_pct=max_w_pct)
        return LateralSpread(
            mode="free_face", magnitude=eq.magnitude, distance_km=r, t15_m=t15, f15_pct=f15,
            d50_15_mm=d50, free_face_height_m=h, profile=tuple(pts), extent_m=extent,
            threshold_m=threshold_m, max_displacement_m=pts[0].displacement_m,
            warnings=tuple(warn),
        )  # fmt: skip
    if slope_pct is None:
        raise ValueError("give free_face_height_m or slope_pct")
    dh = youd2002(eq.magnitude, r, t15, f15, d50, slope_pct=slope_pct)
    return LateralSpread(
        mode="ground_slope", magnitude=eq.magnitude, distance_km=r, t15_m=t15, f15_pct=f15,
        d50_15_mm=d50, slope_pct=slope_pct,
        profile=(SpreadPoint(distance_m=0.0, displacement_m=dh),), extent_m=math.inf,
        threshold_m=threshold_m, max_displacement_m=dh,
        warnings=tuple(range_warnings(eq.magnitude, t15, f15, d50, slope_pct=slope_pct)),
    )  # fmt: skip
