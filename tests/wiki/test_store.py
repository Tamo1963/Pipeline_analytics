"""Wiki store: CRUD, FTS search with snippets, bot tool."""

from pathlib import Path

import pytest

from pipeline_analytics.bot.study import Study
from pipeline_analytics.bot.tools import run_tool
from pipeline_analytics.wiki import WikiStore


@pytest.fixture
def store(tmp_path: Path) -> WikiStore:
    return WikiStore(tmp_path / "w.db")


def test_crud_and_listing(store: WikiStore) -> None:
    a = store.add("HDD practice", "Entry and exit angles 8-12 deg.", tags="construction, hdd",
                  source="note.pdf")  # fmt: skip
    assert a.id == 1 and store.count() == 1
    b = store.update(a.id, content="Entry 8-12 deg; exit 4-8 deg.", tags="hdd")
    assert b.tags == "hdd" and "exit 4-8" in b.content and b.title == "HDD practice"
    assert store.get(a.id).updated_at >= a.updated_at
    assert [x.id for x in store.list_articles()] == [1]
    store.delete(a.id)
    assert store.count() == 0
    with pytest.raises(KeyError):
        store.get(a.id)
    with pytest.raises(ValueError):
        store.add(" ", "x")


def test_search_ranks_and_snippets(store: WikiStore) -> None:
    store.add("Coating selection", "3LPE is the default external coating for buried lines.")
    store.add("River crossings", "Wide rivers are crossed by HDD; 3LPE coating is abraded "
              "during pullback, so use 3LPP or armor at HDD crossings.")  # fmt: skip
    hits = store.search("3LPE coating")
    assert next(h.title for h in hits) == "Coating selection"  # both terms close together
    assert any("[3LPE]" in h.snippet for h in hits)
    assert store.search("HDD pullback")[0].title == "River crossings"
    assert store.search("") == []
    assert store.search("nonexistentterm") == []
    # odd FTS syntax must not raise
    assert isinstance(store.search('"quote AND (x OR'), list)


def test_like_fallback(store: WikiStore) -> None:
    store.add("A", "soft clay springs per ALA")
    hits = store._like_search(["clay"], 5)
    assert hits and hits[0].title == "A" and "clay" in hits[0].snippet


def test_search_wiki_tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PIPELINE_WIKI_DIR", str(tmp_path / "none"))
    text, err = run_tool(Study(), "search_wiki", {"query": "x"})
    assert not err and "empty" in text
    monkeypatch.setenv("PIPELINE_WIKI_DIR", str(tmp_path))
    store = WikiStore(tmp_path / "pipeline_wiki.db")
    art = store.add("T4S wall thickness note", "PNGRB T4S adopts B31.8 design factors. " * 300)
    text, err = run_tool(Study(), "search_wiki", {"query": "T4S design factors"})
    assert not err and "T4S wall thickness note" in text
    text, err = run_tool(Study(), "search_wiki", {"article_id": art.id})
    assert not err and "truncated at 8,000" in text
    _, err = run_tool(Study(), "search_wiki", {})
    assert err
