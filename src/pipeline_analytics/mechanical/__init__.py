"""Mechanical design of buried steel pipelines.

Methodology: ALA 2001 *Guidelines for the Design of Buried Steel Pipe* (American
Lifelines Alliance), with pressure design per ASME B31.4 / B31.8 as adopted by
OISD-STD-141 / OISD-STD-226 / PNGRB T4S. See docs/mechanical-ala.md for the
calculation catalogue, status and values to verify.

Modules:
    pressure        hoop stress, design factor, required wall, MAOP
    soil            soil properties and ALA Appendix B soil springs
    vertical_loads  earth & live load, ring deflection (Iowa), ring buckling
    thermal         restrained/unrestrained longitudinal & combined stress, anchor length
    buoyancy        negative buoyancy, concrete weight coating thickness
    seismic         wave propagation, longitudinal PGD screening, strain limits
    fe              pipe-soil finite-element model (large-displacement beam on ALA springs)
    pgd             fault crossing, transverse PGD, settlement scenarios on the FE model
    fault_analytical  Karamitros et al. (2007) strike-slip method (screening, benchmark)
    assessment      all checks for one segment, with the governing check
"""

from pipeline_analytics.mechanical.assessment import (
    PgdInput,
    SegmentAssessment,
    SegmentInput,
    SeismicInput,
    assess_segment,
    pipe_from_library,
)
from pipeline_analytics.mechanical.buoyancy import BuoyancyInput, buoyancy
from pipeline_analytics.mechanical.common import CheckResult, PipeSection, SteelProperties
from pipeline_analytics.mechanical.pressure import LocationClass, pressure_design
from pipeline_analytics.mechanical.seismic import (
    PerformanceLevel,
    StrainLimits,
    WaveType,
    longitudinal_pgd,
    wave_propagation,
)
from pipeline_analytics.mechanical.soil import Coating, Soil, SoilType, soil_springs
from pipeline_analytics.mechanical.thermal import Restraint, thermal_stress
from pipeline_analytics.mechanical.vertical_loads import VerticalLoadInput, vertical_loads

__all__ = [
    "BuoyancyInput",
    "CheckResult",
    "Coating",
    "LocationClass",
    "PerformanceLevel",
    "PgdInput",
    "PipeSection",
    "Restraint",
    "SegmentAssessment",
    "SegmentInput",
    "SeismicInput",
    "Soil",
    "SoilType",
    "SteelProperties",
    "StrainLimits",
    "VerticalLoadInput",
    "WaveType",
    "assess_segment",
    "buoyancy",
    "longitudinal_pgd",
    "pipe_from_library",
    "pressure_design",
    "soil_springs",
    "thermal_stress",
    "vertical_loads",
    "wave_propagation",
]
