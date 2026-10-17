"""SQLite store + untouched raw file store."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from whitespace.models import Post

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_fetch (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT NOT NULL, key TEXT NOT NULL, url TEXT, fetched_at TEXT NOT NULL,
  path TEXT NOT NULL, parser_version TEXT,
  UNIQUE(source, key)
);
CREATE TABLE IF NOT EXISTS post (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT NOT NULL, source_id TEXT NOT NULL, url TEXT, author_hash TEXT,
  created_at INTEGER, text TEXT, lang TEXT, game_appid INTEGER,
  content_hash TEXT, meta TEXT DEFAULT '{}', flags TEXT DEFAULT '{}',
  UNIQUE(source, source_id)
);
CREATE TABLE IF NOT EXISTS want_item (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  post_id INTEGER NOT NULL REFERENCES post(id),
  request_type TEXT, subject TEXT, want_summary TEXT, referenced_games TEXT, referenced_appids TEXT,
  intensity INTEGER, quote TEXT, confidence REAL, extractor_version TEXT,
  UNIQUE(post_id, want_summary)
);
CREATE TABLE IF NOT EXISTS crawl_cursor (
  source TEXT NOT NULL, scope TEXT NOT NULL, value TEXT, updated_at TEXT,
  PRIMARY KEY (source, scope)
);
CREATE TABLE IF NOT EXISTS run_ledger (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  stage TEXT NOT NULL, scope TEXT, started_at TEXT, finished_at TEXT,
  fetched INTEGER DEFAULT 0, skipped INTEGER DEFAULT 0, error TEXT
);
CREATE TABLE IF NOT EXISTS cluster (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, name TEXT, description TEXT,
  size INTEGER, created_at TEXT
);
CREATE TABLE IF NOT EXISTS cluster_member (
  cluster_id INTEGER REFERENCES cluster(id), want_item_id INTEGER REFERENCES want_item(id),
  prob REAL
);
CREATE TABLE IF NOT EXISTS catalog_game (
  appid INTEGER PRIMARY KEY, name TEXT, tags TEXT, release_date TEXT, price REAL,
  review_count INTEGER, review_score REAL, followers INTEGER, source TEXT
);
CREATE TABLE IF NOT EXISTS niche_score (
  cluster_id INTEGER REFERENCES cluster(id), run_id TEXT,
  demand REAL, dissatisfaction REAL, supply_pressure REAL, gap_score REAL,
  formula_version TEXT
);
"""


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: str | Path = ":memory:") -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    # cursors -------------------------------------------------------------
    def get_cursor(self, source: str, scope: str) -> int | None:
        row = self.conn.execute(
            "SELECT value FROM crawl_cursor WHERE source=? AND scope=?", (source, scope)
        ).fetchone()
        return int(row["value"]) if row and row["value"] is not None else None

    def set_cursor(self, source: str, scope: str, value: int) -> None:
        self.conn.execute(
            "INSERT INTO crawl_cursor(source, scope, value, updated_at) VALUES (?,?,?,?) "
            "ON CONFLICT(source, scope) DO UPDATE SET value=excluded.value, "
            "updated_at=excluded.updated_at",
            (source, scope, str(value), now_iso()),
        )
        self.conn.commit()

    # raw ----------------------------------------------------------------
    def has_raw(self, source: str, key: str) -> bool:
        return (
            self.conn.execute(
                "SELECT 1 FROM raw_fetch WHERE source=? AND key=?", (source, key)
            ).fetchone()
            is not None
        )

    def insert_raw(self, source: str, key: str, url: str, path: Path, parser_version: str) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO raw_fetch(source, key, url, fetched_at, path, parser_version) "
            "VALUES (?,?,?,?,?,?)",
            (source, key, url, now_iso(), str(path), parser_version),
        )
        self.conn.commit()

    # posts --------------------------------------------------------------
    def upsert_post(self, p: Post) -> bool:
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO post(source, source_id, url, author_hash, created_at, text, "
            "lang, game_appid, content_hash, meta) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                p.source,
                p.source_id,
                p.url,
                p.author_hash,
                p.created_at,
                p.text,
                p.lang,
                p.game_appid,
                p.content_hash,
                json.dumps(p.meta),
            ),
        )
        self.conn.commit()
        return cur.rowcount == 1

    def posts_without_flags(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM post WHERE flags = '{}' ORDER BY created_at"
        ).fetchall()

    def set_flags(self, post_id: int, lang: str, flags: dict) -> None:
        self.conn.execute(
            "UPDATE post SET flags=?, lang=? WHERE id=?", (json.dumps(flags), lang, post_id)
        )
        self.conn.commit()

    def count(self, table: str) -> int:
        assert re.fullmatch(r"[a-z_]+", table)
        return self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


class RawStore:
    """Writes fetched bodies untouched: <root>/<source>/<YYYY-MM-DD>/<key>.<ext>."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def save(self, source: str, key: str, content: str | bytes, ext: str) -> Path:
        day = datetime.now(UTC).strftime("%Y-%m-%d")
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", key)
        path = self.root / source / day / f"{safe}.{ext}"
        path.parent.mkdir(parents=True, exist_ok=True)
        data = content.encode("utf-8") if isinstance(content, str) else content
        path.write_bytes(data)
        return path
