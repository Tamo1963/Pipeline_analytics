"""Route elevation profile used by the hydraulic models."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import model_validator

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
