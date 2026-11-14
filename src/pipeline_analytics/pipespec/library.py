"""Query API over the PipeSpec seed data."""

from __future__ import annotations

import math
from collections.abc import Iterable
from enum import StrEnum
from functools import cache
from importlib import resources
from typing import Any

import yaml

from pipeline_analytics.pipespec.models import (
    Coating,
    DeliveryCondition,
    Grade,
    ManufacturingProcess,
    PipeOption,
    PipeSize,
    Standard,
    WallThickness,
)

_CONDITIONS = {c.value for c in DeliveryCondition}


class WtPolicy(StrEnum):
    """How a calculated wall thickness is rounded up to an orderable one."""

    SCHEDULE = "schedule"  # next ASME B36.10M designated thickness
    INCREMENT = "increment"  # next mill increment (plate-based LSAW/HSAW, HFW)


def _load_yaml(name: str) -> Any:
    text = resources.files("pipeline_analytics.pipespec").joinpath("data", name).read_text()
    return yaml.safe_load(text)


class PipeSpecLibrary:
    """In-memory, read-only pipe specification library."""

    def __init__(
        self,
        standards: Iterable[Standard],
        grades: Iterable[Grade],
        sizes: Iterable[PipeSize],
        processes: Iterable[ManufacturingProcess],
        coatings: Iterable[Coating],
    ) -> None:
        self.standards = {s.id: s for s in standards}
        self.grades = sorted(grades, key=lambda g: (g.standard, g.psl or 0, g.smys))
        self.sizes = sorted(sizes, key=lambda s: s.od_mm)
        self.processes = {p.code: p for p in processes}
        self.coatings = {c.code: c for c in coatings}
        self._check_references()

    @classmethod
    def from_seed_data(cls) -> PipeSpecLibrary:
        b36 = _load_yaml("b36_10m.yaml")
        sizes = [
            PipeSize(
                standard=b36["standard"],
                nps=row["nps"],
                dn=row["dn"],
                od_in=row["od_in"],
                od_mm=row["od_mm"],
                wall_thicknesses=tuple(
                    WallThickness(wt_in=wt, designations=tuple(str(d) for d in des))
                    for wt, des in row["wt"]
                ),
            )
            for row in b36["sizes"]
        ]
        return cls(
            standards=[Standard(**s) for s in _load_yaml("standards.yaml")["standards"]],
            grades=[Grade(**g) for g in _load_yaml("grades.yaml")["grades"]],
            sizes=sizes,
            processes=[
                ManufacturingProcess(**p) for p in _load_yaml("processes.yaml")["processes"]
            ],
            coatings=[Coating(**c) for c in _load_yaml("coatings.yaml")["coatings"]],
        )

    def _check_references(self) -> None:
        refs = {g.standard for g in self.grades} | {s.standard for s in self.sizes}
        refs |= {c.standard for c in self.coatings.values()}
        missing = refs - self.standards.keys()
        if missing:
            raise ValueError(f"records reference unknown standards: {sorted(missing)}")

    # ------------------------------------------------------------------ grades
    def find_grades(
        self,
        standard: str | None = None,
        psl: int | None = None,
        min_smys: float | None = None,
        max_smys: float | None = None,
        condition: DeliveryCondition | str | None = None,
    ) -> list[Grade]:
        """Grades matching all given filters, ordered by SMYS."""
        cond = DeliveryCondition(condition) if condition else None
        return [
            g
            for g in self.grades
            if (standard is None or g.standard == standard)
            and (psl is None or g.psl == psl)
            and (min_smys is None or g.smys >= min_smys)
            and (max_smys is None or g.smys <= max_smys)
            and (cond is None or cond in g.conditions)
        ]

    def grade(self, designation: str, psl: int | None = None, standard: str = "API-5L") -> Grade:
        """Look up a grade by any common designation.

        Accepts ``"X70"``, ``"L485"``, ``"X70M"``, ``"L485M"`` and ``"L485M/X70M"``.
        A delivery-condition suffix implies PSL2. Without a suffix or ``psl``,
        PSL2 is preferred when it exists.
        """
        key = designation.strip().upper().split("/")[-1].strip()
        candidates = [g for g in self.grades if g.standard == standard]
        cond: str | None = None
        matches = [g for g in candidates if key in (g.name.upper(), (g.iso_name or "").upper())]
        if not matches and key[-1:] in _CONDITIONS:
            cond, key = key[-1], key[:-1]
            matches = [
                g
                for g in candidates
                if key in (g.name.upper(), (g.iso_name or "").upper())
                and DeliveryCondition(cond) in g.conditions
            ]
        if psl is not None:
            matches = [g for g in matches if g.psl == psl]
        elif len(matches) > 1:
            matches = [g for g in matches if g.psl == 2] or matches
        if not matches:
            raise KeyError(f"no {standard} grade {designation!r}" + (f" PSL{psl}" if psl else ""))
        return matches[0]

    # ------------------------------------------------------------------- sizes
    def size(self, nps: str | float | None = None, *, dn: int | None = None) -> PipeSize:
        """Pipe size by NPS (``"1-1/4"``, ``24`` or ``24.0``) or DN."""
        for s in self.sizes:
            if dn is not None and s.dn == dn:
                return s
            if nps is not None and (
                s.nps == str(nps) or (not isinstance(nps, str) and s.nps_value == float(nps))
            ):
                return s
        raise KeyError(f"no pipe size NPS={nps!r} DN={dn!r}")

    def size_by_od(self, od_mm: float, tol_mm: float = 0.5) -> PipeSize:
        for s in self.sizes:
            if abs(s.od_mm - od_mm) <= tol_mm:
                return s
        raise KeyError(f"no pipe size with OD {od_mm} mm")

    def next_wall_thickness(
        self,
        size: PipeSize,
        required_mm: float,
        policy: WtPolicy = WtPolicy.SCHEDULE,
        step_mm: float = 0.1,
    ) -> float:
        """Smallest orderable wall thickness (mm) not less than ``required_mm``."""
        if required_mm <= 0:
            raise ValueError("required wall thickness must be positive")
        if policy is WtPolicy.INCREMENT:
            # round() guards against float noise such as 9.200000000000001
            wt = round(math.ceil(round(required_mm / step_mm, 9)) * step_mm, 6)
            if wt >= size.od_mm / 2:
                raise ValueError(f"{wt} mm is not a valid wall for OD {size.od_mm} mm")
            return wt
        for w in sorted(size.wall_thicknesses, key=lambda w: w.wt_in):
            if w.wt_mm >= required_mm - 1e-9:
                return w.wt_mm
        raise ValueError(
            f"NPS {size.nps}: no designated wall thickness >= {required_mm} mm "
            f"(max {max(w.wt_mm for w in size.wall_thicknesses)} mm); use WtPolicy.INCREMENT"
        )

    # --------------------------------------------------------------- processes
    def processes_for(
        self, od_mm: float, wt_mm: float | None = None, psl: int | None = None
    ) -> list[ManufacturingProcess]:
        return [
            p
            for p in self.processes.values()
            if p.can_make(od_mm, wt_mm) and (psl is None or psl in p.psl)
        ]

    def coatings_for(self, design_temp_c: float) -> list[Coating]:
        """Corrosion coatings whose indicative temperature range covers ``design_temp_c``."""
        return [
            c
            for c in self.coatings.values()
            if c.function == "corrosion"
            and c.temp_min_c is not None
            and c.temp_max_c is not None
            and c.temp_min_c <= design_temp_c <= c.temp_max_c
        ]

    # ------------------------------------------------------------ combined find
    def find(
        self,
        nps: str | float,
        *,
        psl: int | None = 2,
        min_grade: str | None = None,
        process: str | None = None,
        min_wt_mm: float = 0.0,
        max_wt_mm: float | None = None,
    ) -> list[PipeOption]:
        """Candidate pipes for a size, using ASME B36.10M designated thicknesses.

        Example: PSL2 grades >= X65 as HSAW at NPS 36 with WT <= 15.9 mm::

            lib.find(36, min_grade="X65", process="SAWH", max_wt_mm=15.9)
        """
        size = self.size(nps)
        min_smys = self.grade(min_grade, psl=psl).smys if min_grade else None
        grades = self.find_grades(standard="API-5L", psl=psl, min_smys=min_smys)
        procs = [self.processes[process]] if process else list(self.processes.values())
        options: list[PipeOption] = []
        for wt in size.wall_thicknesses:
            if wt.wt_mm < min_wt_mm or (max_wt_mm is not None and wt.wt_mm > max_wt_mm):
                continue
            for proc in procs:
                if not proc.can_make(size.od_mm, wt.wt_mm):
                    continue
                for g in grades:
                    if g.psl is not None and g.psl not in proc.psl:
                        continue
                    options.append(PipeOption(size=size, wt_mm=wt.wt_mm, grade=g, process=proc))
        return options

    def unverified_count(self) -> int:
        return sum(not g.verified for g in self.grades)


@cache
def default_library() -> PipeSpecLibrary:
    """The library built from the bundled seed data (cached)."""
    return PipeSpecLibrary.from_seed_data()
