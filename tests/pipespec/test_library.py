import pytest
from hypothesis import given
from hypothesis import strategies as st

from pipeline_analytics.pipespec import DeliveryCondition, WtPolicy, default_library

LIB = default_library()


@pytest.mark.parametrize("des", ["X70", "x70", "L485", "X70M", "L485M", "L485Q/X70Q", "X70Q"])
def test_grade_lookup_variants(des: str) -> None:
    g = LIB.grade(des)
    assert g.name == "X70"
    assert g.psl == 2


def test_grade_lookup_psl1() -> None:
    assert LIB.grade("X70", psl=1).psl == 1


def test_grade_b_with_condition() -> None:
    g = LIB.grade("BN")
    assert g.name == "B"
    assert g.psl == 2


def test_grade_a25p_is_not_a_condition_suffix() -> None:
    assert LIB.grade("A25P").name == "A25P"


@pytest.mark.parametrize("des", ["X70R", "X99", "X120N"])
def test_grade_lookup_rejects_unknown(des: str) -> None:
    with pytest.raises(KeyError):
        LIB.grade(des)


def test_grade_x80_not_psl1() -> None:
    with pytest.raises(KeyError):
        LIB.grade("X80", psl=1)


def test_is3589_grade() -> None:
    g = LIB.grade("Fe410", standard="IS-3589")
    assert g.smys == 250


def test_find_grades_filters() -> None:
    names = [g.name for g in LIB.find_grades(psl=2, min_smys=450)]
    assert names == ["X65", "X70", "X80", "X90", "X100", "X120"]
    assert [g.name for g in LIB.find_grades(psl=2, condition="R")] == ["B", "X42"]
    assert [g.name for g in LIB.find_grades(psl=2, condition=DeliveryCondition.NORMALIZED)][-1] == (
        "X60"
    )


def test_designations() -> None:
    assert LIB.grade("X70").designations() == ["L485Q/X70Q", "L485M/X70M"]
    assert LIB.grade("X52", psl=1).designations() == ["L360/X52"]


@pytest.mark.parametrize(("key", "nps"), [("1-1/4", "1-1/4"), (1.25, "1-1/4"), (24, "24")])
def test_size_lookup(key: str | float, nps: str) -> None:
    assert LIB.size(key).nps == nps


def test_size_by_dn_and_od() -> None:
    assert LIB.size(dn=600).nps == "24"
    assert LIB.size_by_od(323.9).nps == "12"
    with pytest.raises(KeyError):
        LIB.size_by_od(300.0)
    with pytest.raises(KeyError):
        LIB.size("23")


def test_wt_by_schedule_prefix() -> None:
    assert LIB.size(8).wt("Sch 40").wt_mm == 8.18
    with pytest.raises(KeyError):
        LIB.size(8).wt("Sch 5")


def test_next_wall_thickness_schedule() -> None:
    s = LIB.size(24)
    assert LIB.next_wall_thickness(s, 9.53) == 9.53
    assert LIB.next_wall_thickness(s, 9.6) == 12.70
    with pytest.raises(ValueError, match="INCREMENT"):
        LIB.next_wall_thickness(LIB.size(40), 14.0)


def test_next_wall_thickness_increment() -> None:
    s = LIB.size(40)
    assert LIB.next_wall_thickness(s, 14.0, WtPolicy.INCREMENT) == 14.0
    assert LIB.next_wall_thickness(s, 14.01, WtPolicy.INCREMENT) == 14.1
    assert LIB.next_wall_thickness(s, 14.01, WtPolicy.INCREMENT, step_mm=0.5) == 14.5
    with pytest.raises(ValueError):
        LIB.next_wall_thickness(s, 0.0, WtPolicy.INCREMENT)


@given(st.floats(min_value=2.0, max_value=40.0))
def test_increment_rounding_property(required: float) -> None:
    wt = LIB.next_wall_thickness(LIB.size(36), required, WtPolicy.INCREMENT, step_mm=0.1)
    assert required - 1e-9 <= wt < required + 0.1 + 1e-9


def test_processes_for_size() -> None:
    assert {p.code for p in LIB.processes_for(168.3)} == {"SMLS", "HFW"}
    assert {p.code for p in LIB.processes_for(914.0)} == {"SAWL", "SAWH"}
    assert {p.code for p in LIB.processes_for(508.0, 12.7)} == {"SMLS", "HFW", "SAWL", "SAWH"}
    assert {p.code for p in LIB.processes_for(508.0, 30.0)} == {"SMLS", "SAWL"}


def test_coatings_for_temperature() -> None:
    assert {c.code for c in LIB.coatings_for(60)} == {"3LPE", "3LPP", "FBE"}
    assert {c.code for c in LIB.coatings_for(100)} == {"3LPP"}


def test_find_plan_example() -> None:
    """PSL2 >= X65, HSAW, NPS 36, WT <= 15.9 mm (example from docs/PLAN.md)."""
    opts = LIB.find(36, min_grade="X65", process="SAWH", max_wt_mm=15.9)
    assert {o.wt_mm for o in opts} == {7.92, 9.53, 12.70, 15.88}
    assert {o.grade.name for o in opts} == {"X65", "X70", "X80", "X90", "X100", "X120"}
    assert all(o.process.code == "SAWH" for o in opts)


def test_find_respects_process_od_range() -> None:
    assert LIB.find(36, process="SMLS") == []
    assert {o.process.code for o in LIB.find(6)} == {"SMLS", "HFW"}
