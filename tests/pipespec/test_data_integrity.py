"""Consistency checks on the seed data. These catch typos in the YAML tables."""

import re

import pytest

from pipeline_analytics.core.units import Q_
from pipeline_analytics.pipespec import default_library

LIB = default_library()

NPS_1_2_TO_48 = [
    "1/2", "3/4", "1", "1-1/4", "1-1/2", "2", "2-1/2", "3", "3-1/2", "4", "5", "6", "8",
    "10", "12", "14", "16", "18", "20", "22", "24", "26", "28", "30", "32", "34", "36",
    "38", "40", "42", "44", "46", "48",
]  # fmt: skip


def test_all_b36_10m_sizes_half_to_48_loaded() -> None:
    assert [s.nps for s in LIB.sizes] == NPS_1_2_TO_48


@pytest.mark.parametrize("size", LIB.sizes, ids=lambda s: s.nps)
def test_od_inch_and_mm_agree(size) -> None:  # type: ignore[no-untyped-def]
    assert size.od_mm == pytest.approx(size.od_in * 25.4, abs=0.6)


def test_dn_and_od_strictly_increasing() -> None:
    dns = [s.dn for s in LIB.sizes]
    ods = [s.od_mm for s in LIB.sizes]
    assert dns == sorted(set(dns))
    assert ods == sorted(set(ods))


@pytest.mark.parametrize("size", LIB.sizes, ids=lambda s: s.nps)
def test_wall_thickness_series_is_consistent(size) -> None:  # type: ignore[no-untyped-def]
    wts = [w.wt_in for w in size.wall_thicknesses]
    assert wts == sorted(set(wts)), "thicknesses must be unique and ascending"
    assert all(w < size.od_in / 2 for w in wts)
    # numbered schedules get thicker as the number rises
    sched = [(int(w.schedule), w.wt_in) for w in size.wall_thicknesses if w.schedule]
    assert [t for _, t in sorted(sched)] == sorted(t for _, t in sched)
    # STD <= XS <= XXS, and every size has STD and XS
    named = {d: w.wt_in for w in size.wall_thicknesses for d in w.designations}
    assert named["STD"] < named["XS"]
    if "XXS" in named:
        assert named["XS"] < named["XXS"]
    # each designation used once per size
    all_des = [d for w in size.wall_thicknesses for d in w.designations]
    assert len(all_des) == len(set(all_des))


def test_std_is_0375_from_nps_10_up() -> None:
    for s in LIB.sizes:
        if s.nps_value >= 12:
            assert s.wt("STD").wt_in == 0.375
            assert s.wt("XS").wt_in == 0.500


def test_api5l_iso_name_matches_smys() -> None:
    for g in LIB.find_grades(standard="API-5L"):
        assert g.iso_name is not None
        assert int(re.sub(r"\D", "", g.iso_name)) == g.smys


def test_api5l_x_grade_number_is_ksi() -> None:
    """X70 -> 70 ksi ~ 483 MPa: the SI value in the standard is within 1.5%."""
    for g in LIB.find_grades(standard="API-5L"):
        if g.name.startswith("X"):
            ksi = int(g.name[1:])
            assert g.smys == pytest.approx(Q_(ksi, "ksi").to("MPa").magnitude, rel=0.015)


@pytest.mark.parametrize("psl", [1, 2])
def test_strength_rises_with_grade(psl: int) -> None:
    grades = [g for g in LIB.find_grades(standard="API-5L", psl=psl) if g.name != "A25P"]
    assert [g.smys for g in grades] == sorted({g.smys for g in grades})
    assert [g.smts for g in grades] == sorted(g.smts for g in grades)


def test_psl1_and_psl2_minimums_match() -> None:
    psl1 = {g.name: g for g in LIB.find_grades(standard="API-5L", psl=1)}
    for g2 in LIB.find_grades(standard="API-5L", psl=2):
        if g2.name in psl1:
            assert g2.smys == psl1[g2.name].smys
            assert g2.smts == psl1[g2.name].smts


def test_psl2_has_maxima_and_yt() -> None:
    for g in LIB.find_grades(standard="API-5L", psl=2):
        assert g.smys_max and g.smts_max and g.yt_max
        assert g.smys / g.smts <= g.yt_max


def test_processes_have_valid_ranges() -> None:
    for p in LIB.processes.values():
        assert p.od_mm[0] < p.od_mm[1]
        assert p.wt_mm[0] < p.wt_mm[1]
        assert p.joint_factor == 1.0


def test_all_records_unverified_until_checked() -> None:
    # Flip `verified: true` in grades.yaml only after checking a licensed copy.
    assert LIB.unverified_count() == len(LIB.grades)
