"""Crawler framework: rate limiting, incremental cursors, untouched raw storage."""

from __future__ import annotations

import random
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from gamegap.models import Post
from gamegap.storage import Database, RawStore, now_iso


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

    def run(self) -> RunStats:
        started = now_iso()
        cursor = self.db.get_cursor(self.name, self.scope)
        newest = cursor or 0
        stats = RunStats()
        error: str | None = None
        try:
            for item in self.fetch_since(cursor):
                if self.db.has_raw(self.name, item.key):
                    stats.skipped += 1
                    continue
                path = self.raw.save(self.name, item.key, item.content, item.ext)
                self.db.insert_raw(self.name, item.key, item.url, path, self.parser_version)
                for post in self.parse(item):
                    stats.new_posts += int(self.db.upsert_post(post))
                stats.fetched += 1
                newest = max(newest, item.cursor_value)
            if newest:
                self.db.set_cursor(self.name, self.scope, newest)
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
