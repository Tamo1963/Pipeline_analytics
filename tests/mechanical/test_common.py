import math

import pytest
from pydantic import ValidationError

from pipeline_analytics.core.units import Q_
from pipeline_analytics.mechanical import CheckResult, PipeSection, pipe_from_library


def test_section_properties(pipe24: PipeSection) -> None:
    di = 0.610 - 2 * 0.00953
    assert pipe24.id_m == pytest.approx(di)
    assert pipe24.steel_area_m2 == pytest.approx(math.pi / 4 * (0.610**2 - di**2))
    assert pipe24.second_moment_m4 == pytest.approx(math.pi / 64 * (0.610**4 - di**4))
    # weight agrees with the PipeSpec plain-end weight (141.12 kg/m)
    assert pipe24.weight_n_per_m / 9.80665 == pytest.approx(141.12, rel=1e-3)


def test_section_from_quantities() -> None:
    p = PipeSection.of(Q_(24, "inch"), Q_(0.375, "inch"), Q_(70, "ksi"))
    assert p.od_m == pytest.approx(0.6096)
    assert p.smys_pa == pytest.approx(482.6e6, rel=1e-3)


def test_section_rejects_bad_wall() -> None:
    with pytest.raises(ValidationError):
        PipeSection(od_m=0.1, wt_m=0.06, smys_pa=1e8)


def test_pipe_from_library() -> None:
    p = pipe_from_library(24, 9.53, "X70")
    assert p.od_m == pytest.approx(0.610)
    assert p.wt_m == pytest.approx(0.00953)
    assert p.smys_pa == 485e6


def test_check_result() -> None:
    c = CheckResult(name="x", demand=-90, capacity=100, unit="Pa", ref="r")
    assert c.utilisation == pytest.approx(0.9) and c.passed
    assert not CheckResult(name="x", demand=101, capacity=100, unit="Pa", ref="r").passed
    assert "FAIL" in str(CheckResult(name="x", demand=2, capacity=1, unit="-", ref="r"))
