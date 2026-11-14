"""One-call mechanical assessment of a buried pipeline segment.

Runs every applicable check for one segment and returns them together with
the governing (highest-utilisation) check. The route optimiser calls this per
segment, and the feasibility bot uses it to explain why a wall, grade, cover
or concrete coating changed.

Always run: internal pressure, soil springs, earth/live load (deflection and
buckling), thermal and combined stress.
Run when inputs are given: buoyancy (``buoyancy``), seismic wave propagation
(``seismic``), longitudinal PGD (``pgd``).
"""

from __future__ import annotations

from pydantic import Field

from pipeline_analytics.core.jurisdiction import Jurisdiction, Service
from pipeline_analytics.mechanical.buoyancy import BuoyancyInput, BuoyancyResult, buoyancy
from pipeline_analytics.mechanical.common import CheckResult, PipeSection, _Frozen
from pipeline_analytics.mechanical.pressure import LocationClass, PressureDesign, pressure_design
from pipeline_analytics.mechanical.seismic import (
    LongitudinalPgdResult,
    PerformanceLevel,
    StrainLimits,
    WavePropagationResult,
    WaveType,
    longitudinal_pgd,
    wave_propagation,
)
from pipeline_analytics.mechanical.soil import Coating, Soil, SoilSprings, soil_springs
from pipeline_analytics.mechanical.thermal import Restraint, ThermalResult, thermal_stress
from pipeline_analytics.mechanical.vertical_loads import (
    VerticalLoadInput,
    VerticalLoadResult,
    vertical_loads,
)


class SeismicInput(_Frozen):
    pgv_m_s: float = Field(gt=0)
    pga_m_s2: float = Field(gt=0)
    apparent_velocity_m_s: float = Field(default=2000.0, gt=0)
    wave_type: WaveType = WaveType.SHEAR
    wavelength_m: float | None = Field(default=None, gt=0)
    level: PerformanceLevel = PerformanceLevel.NORMAL_OPERABILITY


class PgdInput(_Frozen):
    ground_displacement_m: float = Field(gt=0)
    block_length_m: float = Field(gt=0)
    level: PerformanceLevel = PerformanceLevel.NORMAL_OPERABILITY


class SegmentInput(_Frozen):
    """Everything needed to assess one buried segment."""

    service: Service
    location_class: LocationClass = LocationClass.CLASS_1_DIV_2
    design_pressure_pa: float = Field(ge=0)
    design_temp_c: float = 50.0
    install_temp_c: float = 25.0
    corrosion_allowance_m: float = Field(default=0.0, ge=0)
    joint_factor: float = Field(default=1.0, gt=0, le=1)
    cover_m: float = Field(gt=0, description="cover to pipe crown")
    soil: Soil
    coating: Coating = Coating.POLYETHYLENE
    modulus_of_soil_reaction_pa: float = Field(default=6.9e6, gt=0, description="E'")
    water_above_crown_m: float = Field(default=0.0, ge=0)
    live_load_pressure_pa: float = Field(default=0.0, ge=0)
    deflection_limit: float = Field(default=0.03, gt=0)
    buoyancy: BuoyancyInput | None = None
    seismic: SeismicInput | None = None
    pgd: PgdInput | None = None
    strain_limits: StrainLimits = StrainLimits()
    jurisdiction: Jurisdiction = Jurisdiction.INDIA


class SegmentAssessment(_Frozen):
    pressure: PressureDesign
    springs: SoilSprings
    vertical: VerticalLoadResult
    thermal: ThermalResult
    buoyancy: BuoyancyResult | None
    wave_propagation: WavePropagationResult | None
    pgd: LongitudinalPgdResult | None
    checks: tuple[CheckResult, ...]

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    @property
    def governing(self) -> CheckResult:
        return max(self.checks, key=lambda c: c.utilisation)

    def summary(self) -> str:
        return "\n".join(str(c) for c in self.checks)


def assess_segment(pipe: PipeSection, seg: SegmentInput) -> SegmentAssessment:
    depth = seg.cover_m + pipe.od_m / 2  # centreline depth for soil springs
    pres = pressure_design(
        pipe,
        seg.design_pressure_pa,
        seg.service,
        seg.location_class,
        joint_factor=seg.joint_factor,
        design_temp_c=seg.design_temp_c,
        allowance=seg.corrosion_allowance_m,
        jurisdiction=seg.jurisdiction,
    )
    springs = soil_springs(pipe.od_m, depth, seg.soil, seg.coating)
    vert = vertical_loads(
        pipe,
        VerticalLoadInput(
            cover_m=seg.cover_m,
            soil_unit_weight_n_m3=seg.soil.unit_weight_n_m3,
            modulus_of_soil_reaction_pa=seg.modulus_of_soil_reaction_pa,
            water_above_crown_m=seg.water_above_crown_m,
            live_load_pressure_pa=seg.live_load_pressure_pa,
            deflection_limit=seg.deflection_limit,
        ),
    )
    therm = thermal_stress(
        pipe,
        seg.design_pressure_pa,
        seg.install_temp_c,
        seg.design_temp_c,
        Restraint.RESTRAINED,
        axial_soil_resistance=springs.axial.max_force_n_per_m,
        temperature_factor=pres.temperature_factor,
    )
    checks: list[CheckResult] = [pres.check, *vert.checks, *therm.checks]

    buoy = None
    if seg.buoyancy is not None:
        buoy = buoyancy(pipe, seg.buoyancy)
        checks.append(buoy.check)

    wave = None
    if seg.seismic is not None:
        s = seg.seismic
        wave = wave_propagation(
            pipe,
            s.pgv_m_s,
            s.pga_m_s2,
            apparent_velocity=s.apparent_velocity_m_s,
            wave_type=s.wave_type,
            axial_soil_resistance=springs.axial.max_force_n_per_m if s.wavelength_m else None,
            wavelength=s.wavelength_m,
            pressure=seg.design_pressure_pa,
            level=s.level,
            limits=seg.strain_limits,
        )
        checks.extend(wave.checks)

    pgd = None
    if seg.pgd is not None:
        pgd = longitudinal_pgd(
            pipe,
            seg.pgd.ground_displacement_m,
            seg.pgd.block_length_m,
            springs.axial.max_force_n_per_m,
            pressure=seg.design_pressure_pa,
            level=seg.pgd.level,
            limits=seg.strain_limits,
        )
        checks.extend(pgd.checks)

    return SegmentAssessment(
        pressure=pres,
        springs=springs,
        vertical=vert,
        thermal=therm,
        buoyancy=buoy,
        wave_propagation=wave,
        pgd=pgd,
        checks=tuple(checks),
    )


def pipe_from_library(
    nps: str | float, wt_mm: float, grade: str, psl: int | None = 2
) -> PipeSection:
    """Build a :class:`PipeSection` from the PipeSpec Library."""
    from pipeline_analytics.pipespec import default_library

    lib = default_library()
    size = lib.size(nps)
    g = lib.grade(grade, psl=psl)
    return PipeSection(od_m=size.od_mm / 1000, wt_m=wt_mm / 1000, smys_pa=g.smys * 1e6)
