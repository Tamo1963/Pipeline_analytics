import pytest

from pipeline_analytics.codes.class_location import (
    AssemblyPlace,
    Building,
    class_at,
    class_summary,
    classify_route,
    maop_along_route,
)
from pipeline_analytics.mechanical import LocationClass, PipeSection
from pipeline_analytics.routing.geometry import chainage_offset, route_length

C1, C2, C3, C4 = (
    LocationClass.CLASS_1_DIV_2,
    LocationClass.CLASS_2,
    LocationClass.CLASS_3,
    LocationClass.CLASS_4,
)


def houses(n: int, at: float = 5000, spacing: float = 20, offset: float = 50) -> list[Building]:
    return [Building(chainage_m=at + i * spacing, offset_m=offset) for i in range(n)]


def classes(segs: list) -> list[LocationClass]:  # type: ignore[type-arg]
    return [s.location_class for s in segs]


def test_no_buildings_is_class_1() -> None:
    segs = classify_route(10_000)
    assert len(segs) == 1 and segs[0].location_class is C1
    assert (segs[0].start_m, segs[0].end_m) == (0, 10_000)


@pytest.mark.parametrize(("n", "expected"), [(10, C1), (11, C2), (45, C2), (46, C3)])
def test_dwelling_thresholds(n: int, expected: LocationClass) -> None:
    segs = classify_route(20_000, houses(n, spacing=10))
    assert class_at(segs, 5000) is expected


def test_multi_dwelling_units_count_individually() -> None:
    flats = [Building(chainage_m=5000, offset_m=30, dwelling_units=12)]
    assert class_at(classify_route(20_000, flats), 5000) is C2


def test_buildings_outside_200m_ignored() -> None:
    assert class_at(classify_route(20_000, houses(50, offset=210)), 5000) is C1


def test_cluster_boundary_200m() -> None:
    segs = classify_route(20_000, houses(12), step_m=1)
    c2 = next(s for s in segs if s.location_class is C2)
    assert c2.start_m == pytest.approx(5000 - 201.168, abs=1)
    assert c2.end_m == pytest.approx(5220 + 201.168, abs=1)


def test_without_cluster_rule_whole_unit() -> None:
    segs = classify_route(20_000, houses(12), step_m=1, cluster_rule=False)
    c2 = next(s for s in segs if s.location_class is C2)
    # every 1.6 km unit holding >= 11 houses: starts from (5200 - 1609) to (5020 + 1609)
    assert c2.start_m == pytest.approx(5200 - 1609.344, abs=1)
    assert c2.end_m == pytest.approx(5020 + 1609.344, abs=1)


def test_spread_houses_need_window() -> None:
    # 11 houses spread over 3 km: never 11 in one 1.6 km unit
    spread = [Building(chainage_m=1000 + i * 300, offset_m=0) for i in range(11)]
    assert class_summary(classify_route(10_000, spread)) == {C1: 10_000}


def test_class_4_and_assembly() -> None:
    tower = Building(chainage_m=8000, offset_m=100, storeys=5)
    school = AssemblyPlace(chainage_m=12_000, offset_m=40, occupancy=300, name="school")
    small = AssemblyPlace(chainage_m=15_000, offset_m=10, occupancy=10)
    far = AssemblyPlace(chainage_m=17_000, offset_m=95, occupancy=300)
    segs = classify_route(20_000, [tower], [school, small, far], step_m=1)
    assert classes(segs) == [C1, C4, C1, C3, C1]
    c3 = segs[3]
    half = (91.44**2 - 40**2) ** 0.5
    assert c3.start_m == pytest.approx(12_000 - half, abs=1)
    assert c3.end_m == pytest.approx(12_000 + half, abs=1)
    assert "school" in c3.reason


def test_higher_class_wins_and_summary_sums() -> None:
    segs = classify_route(
        20_000, [*houses(50, spacing=5), Building(chainage_m=5100, offset_m=0, storeys=8)]
    )
    assert class_at(segs, 5100) is C4
    assert class_at(segs, 4900) is C3
    assert sum(class_summary(segs).values()) == pytest.approx(20_000)
    with pytest.raises(ValueError):
        class_at(segs, 25_000)


def test_maop_along_route() -> None:
    pipe = PipeSection(od_m=0.610, wt_m=0.00953, smys_pa=485e6)
    segs = classify_route(20_000, houses(50, spacing=5))
    m1, m3 = maop_along_route(segs, pipe, [15_000, 5100])
    assert m1 == pytest.approx(2 * 0.72 * 485e6 * 0.00953 / 0.610)
    assert m3 / m1 == pytest.approx(0.50 / 0.72)


def test_invalid_inputs() -> None:
    with pytest.raises(ValueError):
        classify_route(0)


def test_chainage_offset() -> None:
    route = [(0.0, 0.0), (1000.0, 0.0), (1000.0, 1000.0)]
    assert route_length(route) == 2000
    assert chainage_offset(route, (500, 30)) == pytest.approx((500, 30))  # left of travel
    assert chainage_offset(route, (500, -30)) == pytest.approx((500, -30))
    assert chainage_offset(route, (1100, 500)) == pytest.approx((1500, -100))
    with pytest.raises(ValueError):
        chainage_offset([(0.0, 0.0)], (1, 1))
