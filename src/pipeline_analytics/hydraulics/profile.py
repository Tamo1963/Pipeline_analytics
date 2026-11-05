"""Route elevation profile used by the hydraulic models."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import Field, model_validator

from pipeline_analytics.mechanical.common import _Frozen, interp


class Profile(_Frozen):
    """Chainage (m, ascending from 0) and ground/pipe elevation (m) pairs."""

    chainage_m: tuple[float, ...]
    elevation_m: tuple[float, ...]

    @model_validator(mode="after")
    def _check(self) -> Profile:
        if len(self.chainage_m) < 2 or len(self.chainage_m) != len(self.elevation_m):
            raise ValueError("profile needs >= 2 points with matching chainage and elevation")
        if self.chainage_m[0] != 0:
            raise ValueError("profile must start at chainage 0")
        if any(b <= a for a, b in zip(self.chainage_m, self.chainage_m[1:], strict=False)):
            raise ValueError("chainage must be strictly increasing")
        return self

    @classmethod
    def flat(cls, length_m: float, points: int = 2) -> Profile:
        xs = tuple(length_m * i / (points - 1) for i in range(points))
        return cls(chainage_m=xs, elevation_m=tuple(0.0 for _ in xs))

    @property
    def length_m(self) -> float:
        return self.chainage_m[-1]

    def resample(self, step_m: float) -> Profile:
        """Regular spacing (keeps the end point), e.g. to give station-siting resolution."""
        n = max(1, round(self.length_m / step_m))
        xs = [self.length_m * i / n for i in range(n + 1)]
        # keep original vertices too, so peaks are not cut off
        xs = sorted(set(xs) | set(self.chainage_m))
        return Profile(
            chainage_m=tuple(xs),
            elevation_m=tuple(interp(x, self.chainage_m, self.elevation_m) for x in xs),
        )


def per_point(value: float | Sequence[float], n: int, name: str) -> tuple[float, ...]:
    """Broadcast a scalar to ``n`` points or validate a sequence of length ``n``."""
    if isinstance(value, int | float):
        return tuple(float(value) for _ in range(n))
    out = tuple(float(v) for v in value)
    if len(out) != n:
        raise ValueError(f"{name}: expected {n} values, got {len(out)}")
    return out


class Offtake(_Frozen):
    """Gas or liquid delivered part-way along the line (e.g. a city-gate station)."""

    chainage_m: float = Field(gt=0)
    share: float = Field(gt=0, lt=1, description="fraction of the inlet flow taken off here")
    min_pressure_pa_g: float = Field(default=0.0, description="minimum pressure at the offtake")
    name: str = ""


def with_points(profile: Profile, chainages: Sequence[float]) -> Profile:
    """``profile`` with extra points (elevation interpolated) at ``chainages``."""
    extra = [c for c in chainages if 0 < c < profile.length_m and c not in profile.chainage_m]
    if not extra:
        return profile
    xs = sorted(set(profile.chainage_m) | set(extra))
    return Profile(
        chainage_m=tuple(xs),
        elevation_m=tuple(interp(x, profile.chainage_m, profile.elevation_m) for x in xs),
    )


def segment_fractions(profile: Profile, offtakes: Sequence[Offtake]) -> tuple[float, ...]:
    """Fraction of the inlet flow in each segment k (point k to k+1) after upstream offtakes."""
    if sum(o.share for o in offtakes) >= 1:
        raise ValueError("offtake shares must leave flow for the end delivery point")
    for o in offtakes:
        if o.chainage_m not in profile.chainage_m or o.chainage_m >= profile.length_m:
            raise ValueError(f"offtake at {o.chainage_m} m is not an interior profile point")
    out = []
    for x in profile.chainage_m[:-1]:
        out.append(1.0 - sum(o.share for o in offtakes if o.chainage_m <= x))
    return tuple(out)
