import pytest

from whitespace.ingest.base import RateLimiter, RawItem, SourceCrawler
from whitespace.models import Post


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
