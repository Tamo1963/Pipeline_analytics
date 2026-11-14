"""PipeSpec Library: line-pipe standards, grades, dimensions, processes and coatings.

>>> from pipeline_analytics.pipespec import default_library
>>> lib = default_library()
>>> lib.grade("X70M").smys
485.0
>>> lib.size(24).wt("STD").wt_mm
9.53
"""

from pipeline_analytics.pipespec.library import PipeSpecLibrary, WtPolicy, default_library
from pipeline_analytics.pipespec.models import (
    Coating,
    DeliveryCondition,
    Grade,
    ManufacturingProcess,
    PipeOption,
    PipeSize,
    Standard,
    WallThickness,
    plain_end_weight_kg_m,
)

__all__ = [
    "Coating",
    "DeliveryCondition",
    "Grade",
    "ManufacturingProcess",
    "PipeOption",
    "PipeSize",
    "PipeSpecLibrary",
    "Standard",
    "WallThickness",
    "WtPolicy",
    "default_library",
    "plain_end_weight_kg_m",
]
