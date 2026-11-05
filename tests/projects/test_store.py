import json
import threading
from pathlib import Path

import pytest

from pipeline_analytics.bot.study import Study
from pipeline_analytics.bot.tools import run_tool
from pipeline_analytics.projects import (
    AccessDeniedError,
    ProjectNotFoundError,
    ProjectStore,
    VersionConflictError,
)
from pipeline_analytics.projects.payload import (
    chat_of,
    payload_to_study,
    study_to_payload,
    summary_of,
)

OWNER, ED, VIEW, OTHER = "Owner@Example.com", "ed@example.com", "vi@example.com", "x@example.com"


@pytest.fixture
def store(tmp_path: Path) -> ProjectStore:
    return ProjectStore(tmp_path)


def test_create_list_and_access(store: ProjectStore) -> None:
    p = store.create("Bhola - Khulna", OWNER, "screening", payload={"a": 1})
    assert p.owner == "owner@example.com" and p.latest_version == 1
    assert p.id.startswith("bhola-khulna-")
    store.share(p.id, OWNER, ED, "editor")
    store.share(p.id, OWNER, VIEW, "viewer")
    assert [x.id for x in store.list_for(ED)] == [p.id]
    assert store.list_for(OTHER) == []
    with pytest.raises(ProjectNotFoundError):  # existence is not revealed
        store.get(p.id, OTHER)
    with pytest.raises(AccessDeniedError):
        store.save(p.id, VIEW, {"a": 2}, "try", expected_version=1)
    with pytest.raises(AccessDeniedError):
        store.share(p.id, ED, OTHER, "viewer")  # only the owner shares
    with pytest.raises(ProjectNotFoundError):
        store.get("../etc", OWNER)


def test_versions_are_append_only_with_conflict_check(store: ProjectStore) -> None:
    p = store.create("Line", OWNER)
    store.share(p.id, OWNER, ED, "editor")
    v1 = store.save(p.id, OWNER, {"x": 1}, "first", expected_version=0)
    v2 = store.save(p.id, ED, {"x": 2}, "", expected_version=1)
    assert (v1.version, v2.version, v2.message) == (1, 2, "version 2")
    with pytest.raises(VersionConflictError) as e:  # owner's stale copy
        store.save(p.id, OWNER, {"x": 3}, "stale", expected_version=1)
    assert e.value.latest == 2 and e.value.by == ED
    assert store.load(p.id, OWNER).payload == {"x": 2}
    assert store.load(p.id, OWNER, 1).payload == {"x": 1}
    r = store.restore(p.id, OWNER, 1, expected_version=2)
    assert r.version == 3 and r.payload == {"x": 1} and r.message == "restored version 1"
    hist = store.history(p.id, ED)
    assert [h.version for h in hist] == [1, 2, 3]
    assert [h.author for h in hist] == ["owner@example.com", ED, "owner@example.com"]
    assert store.get(p.id, OWNER).latest_by == "owner@example.com"


def test_concurrent_saves_one_wins(store: ProjectStore) -> None:
    p = store.create("Race", OWNER)
    results: list[str] = []

    def save(i: int) -> None:
        try:
            store.save(p.id, OWNER, {"i": i}, f"s{i}", expected_version=0)
            results.append("ok")
        except VersionConflictError:
            results.append("conflict")

    threads = [threading.Thread(target=save, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == ["conflict"] * 7 + ["ok"]
    assert len(store.history(p.id, OWNER)) == 1


def test_concurrent_shares_are_not_lost(store: ProjectStore) -> None:
    p = store.create("Team", OWNER)
    users = [f"u{i}@example.com" for i in range(8)]
    threads = [threading.Thread(target=store.share, args=(p.id, OWNER, u, "viewer"))
               for u in users]  # fmt: skip
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(store.get(p.id, OWNER).shared_with) == users
    with pytest.raises(ValueError, match="owner"):
        store.share(p.id, OWNER, OWNER, "editor")
    store.unshare(p.id, OWNER, users[0].upper())
    assert users[0] not in store.get(p.id, OWNER).shared_with


def test_archive_and_rename(store: ProjectStore) -> None:
    p = store.create("Old", OWNER)
    store.rename(p.id, OWNER, "New name", "desc")
    store.set_archived(p.id, OWNER)
    assert store.list_for(OWNER) == []
    assert store.list_for(OWNER, include_archived=True)[0].name == "New name"
    with pytest.raises(AccessDeniedError):
        store.save(p.id, OWNER, {}, "x", expected_version=0)


def test_payload_round_trip_through_store(store: ProjectStore) -> None:
    s = Study()
    basis = {"service": "gas", "jurisdiction": "BD", "throughput_start": 150,
             "operating_years": 12, "supply_pressure_barg": 62, "delivery_min_barg": 35,
             "design_pressures_barg": [68.95]}  # fmt: skip
    route = {"name": "Demo", "legs": [{"name": "A-B", "length_km": 40, "minor_roads": 3}]}
    assert not run_tool(s, "set_design_basis", basis)[1]
    assert not run_tool(s, "define_route", route)[1]
    assert not run_tool(s, "evaluate_design", {"nps": "20"})[1]
    payload = study_to_payload(s, chat=[("user", "size it"), ("assistant", "done")])
    json.dumps(payload)  # plain JSON
    p = store.create("Demo", OWNER, payload=payload)
    back = payload_to_study(store.load(p.id, OWNER).payload)
    assert back.basis == s.basis and back.alignment == s.alignment and back.rates == s.rates
    assert len(back.tree.nodes) == len(s.tree.nodes)
    assert chat_of(payload)[1] == ("assistant", "done")
    assert summary_of(payload)["route"] == "Demo"
    # the restored study works: evaluate again
    assert not run_tool(back, "evaluate_design", {"nps": "20"})[1]
    with pytest.raises(ValueError, match="schema"):
        payload_to_study({"schema": 99})
