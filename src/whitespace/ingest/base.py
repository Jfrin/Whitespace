"""Crawler framework: rate limiting, incremental cursors, untouched raw storage."""

from __future__ import annotations

import random
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from whitespace.models import Post
from whitespace.storage import Database, RawStore, now_iso


@dataclass(frozen=True)
class RawItem:
    key: str  # unique per source (post id, page hash, ...)
    content: str | bytes
    ext: str  # "json" | "html"
    url: str
    cursor_value: int  # monotonically increasing (timestamp or id)


@dataclass
class RunStats:
    fetched: int = 0
    skipped: int = 0
    new_posts: int = 0
    replayed: int = 0
    complete: bool = True


class RateLimiter:
    """Minimum interval between calls with jitter. Clock/sleep are injectable for tests."""

    def __init__(
        self,
        min_interval_s: float,
        jitter_s: float = 0.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.min_interval_s = min_interval_s
        self.jitter_s = jitter_s
        self._clock, self._sleep = clock, sleep
        self._last: float | None = None

    def wait(self) -> None:
        if self._last is not None:
            gap = self.min_interval_s + random.uniform(0, self.jitter_s)  # noqa: S311
            remaining = gap - (self._clock() - self._last)
            if remaining > 0:
                self._sleep(remaining)
        self._last = self._clock()


class SourceCrawler(ABC):
    name: str
    parser_version = "1"

    def __init__(self, db: Database, raw: RawStore, limiter: RateLimiter, scope: str) -> None:
        self.db, self.raw, self.limiter, self.scope = db, raw, limiter, scope

    @abstractmethod
    def fetch_since(self, cursor: int | None) -> Iterator[RawItem]:
        """Yield items newer than `cursor`, newest first."""

    @abstractmethod
    def parse(self, item: RawItem) -> list[Post]:
        """Pure function: raw item -> normalized posts. Re-runnable on stored raw data."""

    def finish_run(self, newest: int) -> bool:
        if newest:
            self.db.set_cursor(self.name, self.scope, newest)
        return True

    def run(self) -> RunStats:
        started = now_iso()
        cursor = self.db.get_cursor(self.name, self.scope)
        newest = cursor or 0
        stats = RunStats()
        error: str | None = None
        try:
            # A previous run may have stored the body but died during parsing or post writes.
            # Replay before fetching: the API may no longer return those older items.
            for pending in self.db.pending_raw(self.name, self.scope):
                path = Path(pending["path"])
                if pending["pending_content"] is not None:
                    # The previous write may have been interrupted or never started.
                    self.raw.write(path, pending["pending_content"])
                item = RawItem(
                    pending["key"],
                    path.read_bytes(),
                    path.suffix.lstrip("."),
                    pending["url"],
                    pending["cursor_value"] or 0,
                )
                stats.new_posts += self.db.complete_raw(pending["id"], self.parse(item))
                stats.replayed += 1
                newest = max(newest, item.cursor_value)
            for item in self.fetch_since(cursor):
                if self.db.has_raw(self.name, item.key):
                    stats.skipped += 1
                    newest = max(newest, item.cursor_value)
                    continue
                path = self.raw.target_path(self.name, item.key, item.ext)
                content = (
                    item.content.encode("utf-8") if isinstance(item.content, str) else item.content
                )
                raw_id = self.db.insert_raw(
                    self.name,
                    item.key,
                    item.url,
                    path,
                    self.parser_version,
                    self.scope,
                    item.cursor_value,
                    content,
                )
                self.raw.write(path, content)
                stats.new_posts += self.db.complete_raw(raw_id, self.parse(item))
                stats.fetched += 1
                newest = max(newest, item.cursor_value)
            stats.complete = self.finish_run(newest)
        except Exception as exc:  # ledger the failure, then re-raise
            error = repr(exc)
            raise
        finally:
            self.db.conn.execute(
                "INSERT INTO run_ledger(stage, scope, started_at, finished_at, fetched, skipped, "
                "error) VALUES (?,?,?,?,?,?,?)",
                (
                    f"ingest:{self.name}",
                    self.scope,
                    started,
                    now_iso(),
                    stats.fetched,
                    stats.skipped,
                    error,
                ),
            )
            self.db.conn.commit()
        return stats


class PaginatedCrawler(SourceCrawler):
    """Checkpoint complete pages without advancing the high-water mark until the crawl finishes."""

    def __init__(self, db, raw, limiter, scope, *, backfill=False, variant=""):
        super().__init__(db, raw, limiter, scope)
        self.backfill, self.variant = backfill, variant
        self._page_complete = False
        self._page_newest = 0

    def begin_pages(self, cursor: int | None, initial_token: str | None):
        self._page_complete = False
        state = self.db.conn.execute(
            "SELECT * FROM crawl_page_state WHERE source=? AND scope=?", (self.name, self.scope)
        ).fetchone()
        if state:
            if state["variant"] != self.variant or (self.backfill and not state["backfill"]):
                raise ValueError("Finish the existing crawl before changing its options or mode")
            self._page_newest = state["newest"]
            return state["token"], state["cutoff"]
        self._page_newest = cursor or 0
        cutoff = None if self.backfill else cursor
        self.db.conn.execute(
            "INSERT INTO crawl_page_state(source, scope, token, cutoff, newest, backfill, variant) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                self.name,
                self.scope,
                initial_token,
                cutoff,
                self._page_newest,
                self.backfill,
                self.variant,
            ),
        )
        self.db.conn.commit()
        return initial_token, cutoff

    def checkpoint_page(self, token: str) -> None:
        self.db.conn.execute(
            "UPDATE crawl_page_state SET token=?, newest=? WHERE source=? AND scope=?",
            (token, self._page_newest, self.name, self.scope),
        )
        self.db.conn.commit()

    def finish_run(self, newest: int) -> bool:
        if not self._page_complete:
            return False
        newest = max(newest, self._page_newest)
        with self.db.conn:
            if newest:
                self.db.conn.execute(
                    "INSERT INTO crawl_cursor(source, scope, value, updated_at) VALUES (?,?,?,?) "
                    "ON CONFLICT(source, scope) DO UPDATE SET value=excluded.value, "
                    "updated_at=excluded.updated_at",
                    (self.name, self.scope, str(newest), now_iso()),
                )
            self.db.conn.execute(
                "DELETE FROM crawl_page_state WHERE source=? AND scope=?", (self.name, self.scope)
            )
        return True
