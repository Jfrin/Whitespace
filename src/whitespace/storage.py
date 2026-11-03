"""SQLite store + untouched raw file store."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from whitespace.models import CatalogGame, Post

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_fetch (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT NOT NULL, key TEXT NOT NULL, url TEXT, fetched_at TEXT NOT NULL,
  path TEXT NOT NULL, parser_version TEXT, scope TEXT, cursor_value INTEGER,
  processed_at TEXT, pending_content BLOB,
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
  context_appid INTEGER, context_genres TEXT, scope TEXT, source_context TEXT,
  UNIQUE(post_id, want_summary)
);
CREATE TABLE IF NOT EXISTS extraction_attempt (
  post_id INTEGER NOT NULL REFERENCES post(id), extractor_version TEXT NOT NULL,
  outcome TEXT NOT NULL DEFAULT 'unknown',
  PRIMARY KEY (post_id, extractor_version)
);
CREATE TABLE IF NOT EXISTS clean_fingerprint (
  post_id INTEGER PRIMARY KEY REFERENCES post(id), shingle_count INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS clean_index_state (
  id INTEGER PRIMARY KEY CHECK(id=1), ready INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS clean_shingle (
  shingle TEXT NOT NULL, post_id INTEGER NOT NULL REFERENCES post(id),
  PRIMARY KEY (shingle, post_id)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS crawl_cursor (
  source TEXT NOT NULL, scope TEXT NOT NULL, value TEXT, updated_at TEXT,
  PRIMARY KEY (source, scope)
);
CREATE TABLE IF NOT EXISTS crawl_page_state (
  source TEXT NOT NULL, scope TEXT NOT NULL, token TEXT, cutoff INTEGER,
  newest INTEGER NOT NULL, backfill INTEGER NOT NULL, variant TEXT NOT NULL,
  PRIMARY KEY (source, scope)
);
CREATE TABLE IF NOT EXISTS run_ledger (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  stage TEXT NOT NULL, scope TEXT, started_at TEXT, finished_at TEXT,
  fetched INTEGER DEFAULT 0, skipped INTEGER DEFAULT 0, error TEXT
);
CREATE TABLE IF NOT EXISTS stage_run (
  id INTEGER PRIMARY KEY AUTOINCREMENT, stage TEXT NOT NULL, started_at TEXT NOT NULL,
  finished_at TEXT NOT NULL, elapsed_seconds REAL NOT NULL, status TEXT NOT NULL,
  metrics TEXT NOT NULL
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
  appid INTEGER PRIMARY KEY, name TEXT, genres TEXT, tags TEXT, release_date TEXT,
  price REAL, review_count INTEGER, review_score REAL, followers INTEGER, source TEXT,
  fetched_at TEXT
);
CREATE TABLE IF NOT EXISTS steam_user_tags (
  appid INTEGER PRIMARY KEY, tags TEXT, fetched_at TEXT, attempted_at TEXT,
  status TEXT NOT NULL, source TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS steam_review_metric_cursor (
  appid INTEGER PRIMARY KEY, token TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS steam_review_metric_hydration (
  raw_id INTEGER PRIMARY KEY REFERENCES raw_fetch(id)
);
CREATE TABLE IF NOT EXISTS niche_score (
  cluster_id INTEGER REFERENCES cluster(id), run_id TEXT,
  demand REAL, dissatisfaction REAL, supply_pressure REAL, gap_score REAL,
  formula_version TEXT
);
CREATE TABLE IF NOT EXISTS llm_cache (
  key TEXT PRIMARY KEY, model TEXT, response TEXT NOT NULL, created_at TEXT
);
"""


# Columns added after the first release. CREATE TABLE IF NOT EXISTS never alters an existing
# table, so databases created by older versions get these via ALTER TABLE on open.
MIGRATIONS: list[tuple[str, str, str]] = [
    ("raw_fetch", "scope", "TEXT"),
    ("raw_fetch", "cursor_value", "INTEGER"),
    ("raw_fetch", "processed_at", "TEXT"),
    ("raw_fetch", "pending_content", "BLOB"),
    ("extraction_attempt", "outcome", "TEXT NOT NULL DEFAULT 'unknown'"),
    ("want_item", "referenced_games", "TEXT"),
    ("want_item", "context_appid", "INTEGER"),
    ("want_item", "context_genres", "TEXT"),
    ("want_item", "scope", "TEXT"),
    ("want_item", "source_context", "TEXT"),
    ("catalog_game", "genres", "TEXT"),
    ("catalog_game", "fetched_at", "TEXT"),
]


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: str | Path = ":memory:") -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        # Large temporary queues and duplicate-candidate sets should spill to disk.
        self.conn.execute("PRAGMA temp_store = FILE")
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.executescript("""
            CREATE INDEX IF NOT EXISTS post_game_id ON post(game_appid, id);
            CREATE INDEX IF NOT EXISTS want_post_id ON want_item(post_id);
            CREATE INDEX IF NOT EXISTS post_flags_created ON post(flags, created_at, id);
            CREATE INDEX IF NOT EXISTS post_content_hash ON post(content_hash);
            CREATE INDEX IF NOT EXISTS raw_pending ON raw_fetch(source, scope, processed_at, id);
            CREATE INDEX IF NOT EXISTS clean_shingle_post ON clean_shingle(post_id);
        """)

    def _migrate(self) -> None:
        for table, column, ddl in MIGRATIONS:
            cols = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            if column not in cols:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        self.conn.commit()

    def enable_versioned_wants(self) -> None:
        """Replace the legacy uniqueness constraint without changing row IDs or extra columns."""
        if self.conn.in_transaction:
            raise ValueError("Commit or roll back pending changes before version migration")
        if self.conn.execute("PRAGMA foreign_keys").fetchone()[0]:
            raise ValueError("Version migration requires foreign keys disabled on this connection")
        indexes = self.conn.execute("PRAGMA index_list(want_item)").fetchall()
        old = []
        version_index = False
        for index in indexes:
            columns = [
                r["name"]
                for r in self.conn.execute("SELECT * FROM pragma_index_info(?)", (index["name"],))
            ]
            if index["unique"] and columns == ["post_id", "want_summary"]:
                if index["partial"]:
                    raise ValueError("Cannot safely migrate a partial want-item uniqueness index")
                old.append(index)
            if (
                index["unique"]
                and not index["partial"]
                and columns == ["post_id", "extractor_version", "want_summary"]
            ):
                version_index = True
        if not old:
            if not version_index:
                with self.conn:
                    self.conn.execute(
                        "CREATE UNIQUE INDEX want_per_version "
                        "ON want_item(post_id, extractor_version, want_summary)"
                    )
            return
        schema = self.conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='want_item'"
        ).fetchone()[0]
        replacement = re.sub(
            r"(?i)\bUNIQUE\s*\(\s*[\"`\[]?post_id[\"`\]]?\s*,\s*"
            r"[\"`\[]?want_summary[\"`\]]?\s*\)",
            "UNIQUE(post_id, extractor_version, want_summary)",
            schema,
        )
        if any(index["origin"] == "u" for index in old) and replacement == schema:
            raise ValueError("Cannot safely migrate the existing want_item uniqueness constraint")
        replacement, changed = re.subn(
            r"(?i)^CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"
            r'(?:"want_item"|`want_item`|\[want_item\]|want_item)',
            "CREATE TABLE want_item_version_migration",
            replacement,
            count=1,
        )
        if changed != 1:
            raise ValueError("Cannot safely migrate the existing want_item table")
        removed = {index["name"] for index in old if index["origin"] == "c"}
        objects = [
            row["sql"]
            for row in self.conn.execute(
                "SELECT name, sql FROM sqlite_master WHERE tbl_name='want_item' "
                "AND type IN ('index','trigger') AND sql IS NOT NULL"
            )
            if row["name"] not in removed
        ]
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            self.conn.execute(replacement)
            self.conn.execute("INSERT INTO want_item_version_migration SELECT * FROM want_item")
            self.conn.execute("DROP TABLE want_item")
            self.conn.execute("ALTER TABLE want_item_version_migration RENAME TO want_item")
            for sql in objects:
                self.conn.execute(sql)
            if not version_index and all(index["origin"] != "u" for index in old):
                self.conn.execute(
                    "CREATE UNIQUE INDEX want_per_version "
                    "ON want_item(post_id, extractor_version, want_summary)"
                )
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            raise

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

    def insert_raw(
        self,
        source: str,
        key: str,
        url: str,
        path: Path,
        parser_version: str,
        scope: str | None = None,
        cursor_value: int | None = None,
        pending_content: bytes | None = None,
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO raw_fetch(source, key, url, fetched_at, path, parser_version, "
            "scope, cursor_value, pending_content) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                source,
                key,
                url,
                now_iso(),
                str(path),
                parser_version,
                scope,
                cursor_value,
                pending_content,
            ),
        )
        self.conn.commit()
        return cur.lastrowid

    def pending_raw(self, source: str, scope: str) -> list[sqlite3.Row]:
        # Legacy raw rows have no scope or completion marker. Replaying them once is necessary:
        # their posts may have been only partly written by an interrupted older run.
        return self.conn.execute(
            "SELECT * FROM raw_fetch WHERE source=? AND (scope=? OR scope IS NULL) "
            "AND processed_at IS NULL ORDER BY id",
            (source, scope),
        ).fetchall()

    def complete_raw(self, raw_id: int, posts: list[Post]) -> int:
        """Commit all parsed posts and the completion marker together."""
        with self.conn:
            added = sum(self.upsert_post(post, commit=False) for post in posts)
            self.conn.execute(
                "UPDATE raw_fetch SET processed_at=?, pending_content=NULL WHERE id=?",
                (now_iso(), raw_id),
            )
        return added

    # posts --------------------------------------------------------------
    def upsert_post(self, p: Post, *, commit: bool = True) -> bool:
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
        if commit:
            self.conn.commit()
        return cur.rowcount == 1

    def posts_without_flags(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM post WHERE flags = '{}' ORDER BY created_at"
        ).fetchall()

    def set_flags(self, post_id: int, lang: str, flags: dict) -> None:
        # A manual flag update may make this post eligible for a fresh index pass.
        if self.conn.execute(
            "SELECT 1 FROM clean_fingerprint WHERE post_id=?", (post_id,)
        ).fetchone():
            self.conn.execute("DELETE FROM clean_shingle WHERE post_id=?", (post_id,))
            self.conn.execute("DELETE FROM clean_fingerprint WHERE post_id=?", (post_id,))
        self.conn.execute("DELETE FROM clean_index_state WHERE id=1")
        self.conn.execute(
            "UPDATE post SET flags=?, lang=? WHERE id=?", (json.dumps(flags), lang, post_id)
        )
        self.conn.commit()

    # catalog ------------------------------------------------------------
    def upsert_catalog(self, g: CatalogGame) -> None:
        self.conn.execute(
            "INSERT INTO catalog_game(appid, name, genres, tags, release_date, price, source, "
            "fetched_at) VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(appid) DO UPDATE SET name=excluded.name, genres=excluded.genres, "
            "tags=excluded.tags, release_date=excluded.release_date, price=excluded.price, "
            "source=excluded.source, fetched_at=excluded.fetched_at",
            (
                g.appid,
                g.name,
                json.dumps(g.genres),
                json.dumps(g.tags),
                g.release_date,
                g.price,
                "steam_appdetails",
                now_iso(),
            ),
        )
        self.conn.commit()

    def catalog_is_fresh(self, appid: int, max_age_days: int) -> bool:
        row = self.conn.execute(
            "SELECT fetched_at FROM catalog_game WHERE appid=?", (appid,)
        ).fetchone()
        if not row or not row["fetched_at"]:
            return False
        age = datetime.now(UTC) - datetime.fromisoformat(row["fetched_at"])
        return age < timedelta(days=max_age_days)

    def catalog_genres(self, appid: int) -> list[str]:
        """Genres for an appid; [] when unknown or the fetch found nothing."""
        row = self.conn.execute(
            "SELECT genres FROM catalog_game WHERE appid=?", (appid,)
        ).fetchone()
        return json.loads(row["genres"]) if row and row["genres"] else []

    # llm cache ----------------------------------------------------------
    def llm_cache_get(self, key: str) -> str | None:
        row = self.conn.execute("SELECT response FROM llm_cache WHERE key=?", (key,)).fetchone()
        return row["response"] if row else None

    def llm_cache_put(self, key: str, model: str, response: str) -> None:
        self.llm_cache_put_many([(key, model, response)])

    def llm_cache_put_many(self, entries: list[tuple[str, str, str]]) -> None:
        with self.conn:
            self.conn.executemany(
                "INSERT OR REPLACE INTO llm_cache(key, model, response, created_at) "
                "VALUES (?,?,?,?)",
                ((key, model, response, now_iso()) for key, model, response in entries),
            )

    def count(self, table: str) -> int:
        assert re.fullmatch(r"[a-z_]+", table)
        return self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


class RawStore:
    """Writes fetched bodies untouched: <root>/<source>/<YYYY-MM-DD>/<key>.<ext>."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def target_path(self, source: str, key: str, ext: str) -> Path:
        day = datetime.now(UTC).strftime("%Y-%m-%d")
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", key)
        return self.root / source / day / f"{safe}.{ext}"

    @staticmethod
    def write(path: Path, content: str | bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = content.encode("utf-8") if isinstance(content, str) else content
        path.write_bytes(data)

    def save(self, source: str, key: str, content: str | bytes, ext: str) -> Path:
        path = self.target_path(source, key, ext)
        self.write(path, content)
        return path
