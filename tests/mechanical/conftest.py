import pytest

from pipeline_analytics.mechanical import PipeSection, Soil, SoilType


@pytest.fixture
def pipe24() -> PipeSection:
    """NPS 24 x 9.53 mm, API 5L X70 (SMYS 485 MPa)."""
    return PipeSection(od_m=0.610, wt_m=0.00953, smys_pa=485e6)


@pytest.fixture
def dense_sand() -> Soil:
    return Soil(soil_type=SoilType.DENSE_SAND, unit_weight_n_m3=18e3, friction_angle_deg=36)


@pytest.fixture
def soft_clay() -> Soil:
    return Soil(soil_type=SoilType.SOFT_CLAY, unit_weight_n_m3=17e3, cohesion_pa=25e3)
