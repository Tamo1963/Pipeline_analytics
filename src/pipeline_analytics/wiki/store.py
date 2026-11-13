"""Pipeline wiki: a knowledgebase of articles in a single SQLite file.

Articles (title, content, tags, source) live in ``articles``; full-text
search uses SQLite's FTS5 with a ``LIKE`` fallback where FTS5 is not
compiled in. The database file defaults to ``$PIPELINE_WIKI_DIR/pipeline_wiki.db``
(or ``./wiki/pipeline_wiki.db``). A connection is opened per operation, so
several app sessions can share the file safely (SQLite serialises writers).

The wiki is plain reference storage: upload a document (PDF, DOCX, text) or
write an article; the bot's ``search_wiki`` tool and the app's Wiki tab search
the same database.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from collections.abc import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""
FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5(
    title, content, tags, content='articles', content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS articles_ai AFTER INSERT ON articles BEGIN
    INSERT INTO articles_fts(rowid, title, content, tags)
    VALUES (new.id, new.title, new.content, new.tags);
END;
CREATE TRIGGER IF NOT EXISTS articles_ad AFTER DELETE ON articles BEGIN
    INSERT INTO articles_fts(articles_fts, rowid, title, content, tags)
    VALUES ('delete', old.id, old.title, old.content, old.tags);
END;
CREATE TRIGGER IF NOT EXISTS articles_au AFTER UPDATE ON articles BEGIN
    INSERT INTO articles_fts(articles_fts, rowid, title, content, tags)
    VALUES ('delete', old.id, old.title, old.content, old.tags);
    INSERT INTO articles_fts(rowid, title, content, tags)
    VALUES (new.id, new.title, new.content, new.tags);
END;
"""


class _M(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Article(_M):
    id: int
    title: str
    content: str
    tags: str = ""
    source: str = ""
    created_at: str = ""
    updated_at: str = ""


class SearchHit(_M):
    id: int
    title: str
    tags: str
    snippet: str
    rank: float = Field(default=0.0, description="lower is better (FTS5 bm25)")


def default_db() -> Path:
    return Path(os.environ.get("PIPELINE_WIKI_DIR", "wiki")) / "pipeline_wiki.db"


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


class WikiStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else default_db()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)
            try:
                c.executescript(FTS)
                self.fts = True
            except sqlite3.OperationalError:  # pragma: no cover - FTS5 not compiled in
                self.fts = False

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        try:
            with conn:  # one transaction per operation
                yield conn
        finally:
            conn.close()

    # -------------------------------------------------------------- articles
    def add(self, title: str, content: str, *, tags: str = "", source: str = "") -> Article:
        title, content = title.strip(), content.strip()
        if not title or not content:
            raise ValueError("an article needs a title and content")
        now = _now()
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO articles (title, content, tags, source, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (title, content, tags.strip(), source.strip(), now, now),
            )
            return self._row(c, int(cur.lastrowid or 0))

    def get(self, article_id: int) -> Article:
        with self._conn() as c:
            return self._row(c, article_id)

    def update(
        self,
        article_id: int,
        *,
        title: str | None = None,
        content: str | None = None,
        tags: str | None = None,
    ) -> Article:
        with self._conn() as c:
            art = self._row(c, article_id)
            c.execute(
                "UPDATE articles SET title = ?, content = ?, tags = ?, updated_at = ? WHERE id = ?",
                (
                    (art.title if title is None else title.strip()) or art.title,
                    (art.content if content is None else content.strip()) or art.content,
                    art.tags if tags is None else tags.strip(),
                    _now(),
                    article_id,
                ),
            )
            return self._row(c, article_id)

    def delete(self, article_id: int) -> None:
        with self._conn() as c:
            self._row(c, article_id)
            c.execute("DELETE FROM articles WHERE id = ?", (article_id,))

    def list_articles(self, limit: int = 200) -> list[Article]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM articles ORDER BY updated_at DESC, id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [Article(**dict(r)) for r in rows]

    def count(self) -> int:
        with self._conn() as c:
            return int(c.execute("SELECT COUNT(*) FROM articles").fetchone()[0])

    @staticmethod
    def _row(c: sqlite3.Connection, article_id: int) -> Article:
        r = c.execute("SELECT * FROM articles WHERE id = ?", (article_id,)).fetchone()
        if r is None:
            raise KeyError(f"no wiki article {article_id}")
        return Article(**dict(r))

    # ---------------------------------------------------------------- search
    def search(self, query: str, limit: int = 8) -> list[SearchHit]:
        """Full-text search; terms are AND-ed, ranked by bm25, with snippets."""
        tokens = [t for t in query.split() if t.strip('"')]
        if not tokens:
            return []
        if self.fts:
            match = " ".join('"' + t.replace('"', "") + '"' for t in tokens)
            try:
                with self._conn() as c:
                    rows = c.execute(
                        "SELECT a.id, a.title, a.tags, bm25(articles_fts) AS rank, "
                        "snippet(articles_fts, 1, '[', ']', ' ... ', 16) AS snip "
                        "FROM articles_fts JOIN articles a ON a.id = articles_fts.rowid "
                        "WHERE articles_fts MATCH ? ORDER BY rank LIMIT ?",
                        (match, limit),
                    ).fetchall()
                return [
                    SearchHit(
                        id=r["id"],
                        title=r["title"],
                        tags=r["tags"],
                        snippet=r["snip"],
                        rank=float(r["rank"]),
                    )
                    for r in rows
                ]
            except sqlite3.OperationalError:
                pass  # fall through to LIKE for queries FTS cannot parse
        return self._like_search(tokens, limit)

    def _like_search(self, tokens: list[str], limit: int) -> list[SearchHit]:
        where = " AND ".join(["(title LIKE ? OR content LIKE ? OR tags LIKE ?)"] * len(tokens))
        args: list[Any] = []
        for t in tokens:
            args += [f"%{t}%"] * 3
        with self._conn() as c:
            rows = c.execute(
                f"SELECT id, title, tags, content FROM articles WHERE {where} "
                "ORDER BY updated_at DESC LIMIT ?",
                (*args, limit),
            ).fetchall()
        hits = []
        for r in rows:
            content = r["content"]
            pos = content.lower().find(tokens[0].lower())
            start = max(0, pos - 60)
            hits.append(SearchHit(id=r["id"], title=r["title"], tags=r["tags"],
                                  snippet=content[start : start + 160]))  # fmt: skip
        return hits


__all__ = ["Article", "SearchHit", "WikiStore", "default_db"]
