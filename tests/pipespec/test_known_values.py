"""Spot checks against widely published ASME B36.10M / API 5L values."""

import pytest

from pipeline_analytics.pipespec import default_library, plain_end_weight_kg_m

LIB = default_library()


@pytest.mark.parametrize(
    ("nps", "des", "wt_mm", "kg_m"),
    [
        ("4", "40", 6.02, 16.07),
        ("6", "40", 7.11, 28.26),
        ("8", "40", 8.18, 42.55),
        ("12", "STD", 9.53, 73.88),
        ("24", "STD", 9.53, 141.12),
        ("2", "80", 5.54, 7.48),
    ],
)
def test_wall_and_weight(nps: str, des: str, wt_mm: float, kg_m: float) -> None:
    size = LIB.size(nps)
    wt = size.wt(des)
    assert wt.wt_mm == wt_mm
    assert size.weight_kg_m(wt.wt_mm) == pytest.approx(kg_m, rel=0.003)


def test_weight_matches_inch_formula() -> None:
    """lb/ft = 10.69 (D - t) t with inches; independent of the metric OD rounding."""
    for s in LIB.sizes:
        for w in s.wall_thicknesses:
            lb_ft = 10.69 * (s.od_in - w.wt_in) * w.wt_in
            assert s.weight_kg_m(w.wt_mm) == pytest.approx(lb_ft * 1.48816, rel=0.004)


def test_x70_psl2() -> None:
    g = LIB.grade("X70", psl=2)
    assert (g.smys, g.smys_max, g.smts, g.smts_max, g.yt_max) == (485, 635, 570, 760, 0.93)


def test_x52_psl1() -> None:
    g = LIB.grade("X52", psl=1)
    assert (g.smys, g.smts) == (360, 460)


def test_weight_rejects_bad_wall() -> None:
    with pytest.raises(ValueError):
        plain_end_weight_kg_m(100.0, 60.0)
