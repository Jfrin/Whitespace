import sqlite3
from pathlib import Path

import pytest

from whitespace.ingest.base import RateLimiter, RawItem, SourceCrawler
from whitespace.models import Post
from whitespace.storage import Database


def test_rate_limiter_sleeps_only_the_remaining_gap():
    t = {"now": 100.0}
    slept: list[float] = []
    rl = RateLimiter(2.0, clock=lambda: t["now"], sleep=slept.append)
    rl.wait()  # first call never sleeps
    t["now"] += 0.5
    rl.wait()  # 0.5s elapsed -> sleep ~1.5
    assert slept == [pytest.approx(1.5)]


class Boom(SourceCrawler):
    name = "boom"

    def fetch_since(self, cursor):
        yield RawItem("k1", "{}", "json", "http://x", 5)
        raise RuntimeError("network died")

    def parse(self, item):
        return [Post("boom", "1", "http://x", "some text here", 5)]


def test_failed_run_is_ledgered_and_cursor_not_advanced(db, raw, limiter):
    with pytest.raises(RuntimeError):
        Boom(db, raw, limiter, scope="s").run()
    row = db.conn.execute("SELECT * FROM run_ledger").fetchone()
    assert "network died" in row["error"] and row["fetched"] == 1
    assert db.get_cursor("boom", "s") is None
    # rerun is safe: the already-stored raw item is skipped, not duplicated
    with pytest.raises(RuntimeError):
        Boom(db, raw, limiter, scope="s").run()
    assert db.count("raw_fetch") == 1 and db.count("post") == 1


class RestartCrawler(SourceCrawler):
    name = "restart"

    def __init__(self, db, raw, limiter, scope="a", *, fail_parse=False, items=()):
        super().__init__(db, raw, limiter, scope)
        self.fail_parse = fail_parse
        self.items = items

    def fetch_since(self, cursor):
        yield from self.items

    def parse(self, item):
        if self.fail_parse:
            raise ValueError("parse failed")
        return [
            Post(self.name, f"{item.key}-{i}", item.url, "text", item.cursor_value)
            for i in range(2)
        ]


def test_restart_replays_unprocessed_raw_even_when_api_no_longer_has_it(tmp_path, raw, limiter):
    path = tmp_path / "crawl.sqlite"
    db = Database(path)
    item = RawItem("first", "{}", "json", "http://x", 10)
    with pytest.raises(ValueError, match="parse failed"):
        RestartCrawler(db, raw, limiter, fail_parse=True, items=[item]).run()
    assert db.count("raw_fetch") == 1 and db.count("post") == 0
    assert db.get_cursor("restart", "a") is None
    db.conn.close()

    resumed = Database(path)
    stats = RestartCrawler(resumed, raw, limiter).run()
    assert stats.replayed == 1 and stats.new_posts == 2 and stats.fetched == 0
    assert resumed.count("post") == 2 and resumed.get_cursor("restart", "a") == 10
    assert resumed.conn.execute("SELECT processed_at FROM raw_fetch").fetchone()[0]
    assert RestartCrawler(resumed, raw, limiter).run().replayed == 0


def test_partial_post_writes_roll_back_with_raw_completion(db, raw, limiter, monkeypatch):
    item = RawItem("first", "{}", "json", "http://x", 10)
    original = db.upsert_post
    calls = 0

    def fail_on_second(post, *, commit=True):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("insert failed")
        return original(post, commit=commit)

    monkeypatch.setattr(db, "upsert_post", fail_on_second)
    with pytest.raises(RuntimeError, match="insert failed"):
        RestartCrawler(db, raw, limiter, items=[item]).run()
    assert db.count("post") == 0 and db.get_cursor("restart", "a") is None
    assert db.conn.execute("SELECT processed_at FROM raw_fetch").fetchone()[0] is None
    monkeypatch.setattr(db, "upsert_post", original)
    assert RestartCrawler(db, raw, limiter).run().new_posts == 2


def test_restart_repairs_interrupted_raw_file_write(tmp_path, raw, limiter, monkeypatch):
    path = tmp_path / "crawl.sqlite"
    db = Database(path)
    item = RawItem("first", '{"important": "response"}', "json", "http://x", 10)
    original = raw.write

    def interrupted_write(path, content):
        original(path, b"partial")
        raise OSError("write interrupted")

    monkeypatch.setattr(raw, "write", interrupted_write)
    with pytest.raises(OSError, match="write interrupted"):
        RestartCrawler(db, raw, limiter, items=[item]).run()
    pending = db.pending_raw("restart", "a")[0]
    assert pending["pending_content"] == item.content.encode()
    assert db.get_cursor("restart", "a") is None
    db.conn.close()

    monkeypatch.setattr(raw, "write", original)
    resumed = Database(path)
    stats = RestartCrawler(resumed, raw, limiter).run()
    assert stats.replayed == 1 and stats.new_posts == 2
    assert Path(pending["path"]).read_bytes() == item.content.encode()
    assert resumed.conn.execute("SELECT pending_content FROM raw_fetch").fetchone()[0] is None


def test_replay_only_own_scope_and_do_not_advance_cursor_on_fetch_failure(db, raw, limiter):
    for scope, key in (("a", "one"), ("b", "two")):
        with pytest.raises(ValueError, match="parse failed"):
            RestartCrawler(
                db,
                raw,
                limiter,
                scope=scope,
                fail_parse=True,
                items=[RawItem(key, "{}", "json", "http://x", 10)],
            ).run()

    class FailingFetch(RestartCrawler):
        def fetch_since(self, cursor):
            raise RuntimeError("fetch failed")
            yield  # pragma: no cover: keep this a generator

    with pytest.raises(RuntimeError, match="fetch failed"):
        FailingFetch(db, raw, limiter).run()
    assert db.count("post") == 2
    assert db.get_cursor("restart", "a") is None
    assert len(db.pending_raw("restart", "b")) == 1
    assert RestartCrawler(db, raw, limiter, scope="b").run().new_posts == 2


def test_legacy_raw_is_replayed_once_after_migration(tmp_path, raw, limiter):
    path = tmp_path / "legacy.sqlite"
    body = raw.save("restart", "old", "{}", "json")
    old = sqlite3.connect(path)
    old.execute(
        "CREATE TABLE raw_fetch (id INTEGER PRIMARY KEY, source TEXT NOT NULL, key TEXT NOT NULL, "
        "url TEXT, fetched_at TEXT NOT NULL, path TEXT NOT NULL, parser_version TEXT, "
        "UNIQUE(source, key))"
    )
    old.execute(
        "INSERT INTO raw_fetch VALUES (1, 'restart', 'old', 'http://x', 'now', ?, '1')",
        (str(body),),
    )
    old.commit()
    old.close()
    db = Database(path)
    stats = RestartCrawler(db, raw, limiter).run()
    assert stats.replayed == 1 and stats.new_posts == 2
    assert RestartCrawler(db, raw, limiter).run().replayed == 0


def test_missing_pending_raw_fails_without_advancing_cursor(db, raw, limiter):
    body = raw.save("restart", "missing", "{}", "json")
    db.insert_raw("restart", "missing", "http://x", body, "1", "a", 10)
    body.unlink()
    with pytest.raises(FileNotFoundError):
        RestartCrawler(db, raw, limiter).run()
    assert db.get_cursor("restart", "a") is None
    assert len(db.pending_raw("restart", "a")) == 1
