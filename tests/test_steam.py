import json

import httpx
import pytest

from whitespace.ingest.steam_reviews import SteamReviewsCrawler
from whitespace.storage import Database


def _review(i, ts, text="I wish this had a proper co-op mode and no grind at all."):
    return {
        "recommendationid": str(i),
        "timestamp_created": ts,
        "review": text,
        "language": "english",
        "voted_up": False,
        "votes_up": 3,
        "author": {"steamid": f"7656{i}", "playtime_forever": 120},
    }


def _client(pages, calls):
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).startswith(
            "https://api.steampowered.com/IUserReviewsService/GetAppReviews/v1/?"
        )
        calls.append(json.loads(request.url.params["input_json"]))
        page = pages[len(calls) - 1] if len(calls) <= len(pages) else {"reviews": []}
        return httpx.Response(200, json={"response": page})

    return httpx.Client(transport=httpx.MockTransport(handler))


PAGES = [
    {"reviews": [_review(3, 300), _review(2, 200)], "cursor": "c+/="},
    {"reviews": [_review(1, 100)], "cursor": "c2"},
    {"reviews": [], "cursor": "c3"},
]


def test_full_crawl_paginates_and_stores(db, raw, limiter):
    calls: list = []
    stats = SteamReviewsCrawler(db, raw, limiter, 42, _client(PAGES, calls)).run()
    assert stats.fetched == 3 and stats.new_posts == 3
    assert calls[0] == {
        "appid": 42,
        "filter": 1,
        "languages": ["all"],
        "review_type": 2,
        "purchase_type": 1,
        "num_per_page": 100,
        "cursor": "*",
    }
    assert calls[1]["cursor"] == "c+/="  # opaque cursor is encoded and threaded through
    assert db.get_cursor("steam_reviews", "42") == 300
    post = db.conn.execute("SELECT * FROM post WHERE source_id='42-3'").fetchone()
    assert post["game_appid"] == 42 and post["author_hash"] and "7656" not in post["author_hash"]


def test_second_run_is_incremental_and_dedupes(db, raw, limiter):
    SteamReviewsCrawler(db, raw, limiter, 42, _client(PAGES, [])).run()
    newer = [{"reviews": [_review(4, 400), _review(3, 300)], "cursor": "x"}]
    stats = SteamReviewsCrawler(db, raw, limiter, 42, _client(newer, [])).run()
    assert stats.fetched == 1  # only review 4; stopped at the cursor
    assert db.count("post") == 4 and db.get_cursor("steam_reviews", "42") == 400


def test_empty_review_text_is_skipped(db, raw, limiter):
    pages = [{"reviews": [_review(9, 900, text="   ")], "cursor": "x"}]
    stats = SteamReviewsCrawler(db, raw, limiter, 1, _client(pages, [])).run()
    assert stats.fetched == 1 and db.count("post") == 0


def test_review_engagement_is_stored_with_recommendation(db, raw, limiter):
    review = {
        **_review(9, 900),
        "votes_up": 34,
        "votes_funny": 1,
        "weighted_vote_score": 0.75,
        "comment_count": 2,
    }
    SteamReviewsCrawler(db, raw, limiter, 1, _client([{"reviews": [review]}], [])).run()
    meta = json.loads(db.conn.execute("SELECT meta FROM post").fetchone()[0])
    assert meta["votes_up"] == 34 and meta["votes_funny"] == 1
    assert meta["weighted_vote_score"] == 0.75 and meta["voted_up"] is False


@pytest.mark.parametrize(
    ("review_type", "expected"), [("all", 0), ("positive", 1), ("negative", 2)]
)
def test_review_type_filter(db, raw, limiter, review_type, expected):
    calls = []
    crawler = SteamReviewsCrawler(db, raw, limiter, 42, _client([], calls), review_type=review_type)
    crawler.run()
    assert calls[0]["review_type"] == expected


def test_unknown_review_type_rejected(db, raw, limiter):
    with pytest.raises(ValueError, match="Unknown review type"):
        SteamReviewsCrawler(db, raw, limiter, 42, _client([], []), review_type="bad")


@pytest.mark.parametrize("payload", [{}, {"response": {"reviews": "invalid"}}])
def test_malformed_response_fails_instead_of_silently_succeeding(db, raw, limiter, payload):
    client = httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json=payload))
    )
    with pytest.raises(ValueError, match="Steam reviews API"):
        SteamReviewsCrawler(db, raw, limiter, 42, client).run()
    assert db.get_cursor("steam_reviews", "42") is None


def test_rate_limit_is_reported_and_does_not_advance_cursor(db, raw, limiter):
    client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(429)))
    with pytest.raises(httpx.HTTPStatusError) as exc:
        SteamReviewsCrawler(db, raw, limiter, 42, client).run()
    assert exc.value.response.status_code == 429
    assert db.get_cursor("steam_reviews", "42") is None


def _token_client(pages, calls):
    def handler(request):
        params = json.loads(request.url.params["input_json"])
        calls.append(params["cursor"])
        return httpx.Response(200, json={"response": pages[params["cursor"]]})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_backfill_resumes_page_limit_then_catches_new_arrivals(tmp_path, raw, limiter):
    path = tmp_path / "checkpoint.sqlite"
    db = Database(path)
    db.set_cursor("steam_reviews", "42", 900)
    pages = {
        "*": {"reviews": [_review(10, 1000), _review(9, 900)], "cursor": "a"},
        "a": {"reviews": [_review(8, 800), _review(7, 700)], "cursor": "b"},
        "b": {"reviews": [_review(6, 600)]},
    }
    calls = []
    client = _token_client(pages, calls)
    first = SteamReviewsCrawler(db, raw, limiter, 42, client, max_pages=1, backfill=True).run()
    assert not first.complete and db.get_cursor("steam_reviews", "42") == 900
    db.conn.close()
    db = Database(path)
    second = SteamReviewsCrawler(db, raw, limiter, 42, client, max_pages=1).run()
    assert not second.complete and db.get_cursor("steam_reviews", "42") == 900
    final = SteamReviewsCrawler(db, raw, limiter, 42, client, max_pages=1).run()
    assert final.complete and db.get_cursor("steam_reviews", "42") == 1000
    assert calls == ["*", "a", "b"] and db.count("post") == 5
    pages["*"] = {"reviews": [_review(11, 1100), _review(10, 1000), _review(9, 900)]}
    assert SteamReviewsCrawler(db, raw, limiter, 42, client).run().complete
    assert db.get_cursor("steam_reviews", "42") == 1100 and db.count("post") == 6


def test_incremental_keeps_timestamp_ties_across_pages(db, raw, limiter):
    db.set_cursor("steam_reviews", "42", 100)
    pages = {
        "*": {"reviews": [_review(3, 200), _review(2, 100)], "cursor": "next"},
        "next": {"reviews": [_review(1, 100), _review(0, 99)]},
    }
    client = _token_client(pages, [])
    assert not SteamReviewsCrawler(db, raw, limiter, 42, client, max_pages=1).run().complete
    assert db.get_cursor("steam_reviews", "42") == 100
    assert SteamReviewsCrawler(db, raw, limiter, 42, client, max_pages=1).run().complete
    assert db.get_cursor("steam_reviews", "42") == 200
    ids = {row[0] for row in db.conn.execute("SELECT source_id FROM post")}
    assert ids == {"42-1", "42-2", "42-3"}


def test_interrupted_page_replays_before_checkpoint(db, raw, limiter, monkeypatch):
    pages = {"*": {"reviews": [_review(2, 200), _review(1, 100)]}}
    client = _token_client(pages, [])
    original = db.complete_raw
    calls = 0

    def fail_second(raw_id, posts):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("interrupted")
        return original(raw_id, posts)

    monkeypatch.setattr(db, "complete_raw", fail_second)
    with pytest.raises(RuntimeError, match="interrupted"):
        SteamReviewsCrawler(db, raw, limiter, 42, client).run()
    assert db.get_cursor("steam_reviews", "42") is None
    monkeypatch.setattr(db, "complete_raw", original)
    stats = SteamReviewsCrawler(db, raw, limiter, 42, client).run()
    assert stats.complete and stats.replayed == 1 and db.count("post") == 2
    assert db.get_cursor("steam_reviews", "42") == 200


def test_repeated_page_token_fails_without_advancing_cursor(db, raw, limiter):
    pages = {"*": {"reviews": [_review(1, 100)], "cursor": "*"}}
    with pytest.raises(ValueError, match="repeated cursor"):
        SteamReviewsCrawler(db, raw, limiter, 42, _token_client(pages, [])).run()
    assert db.get_cursor("steam_reviews", "42") is None


def test_cli_backfill_and_page_budget_resume(monkeypatch, tmp_path, capsys):
    from whitespace.cli import main

    monkeypatch.setenv("WHITESPACE_DATA_DIR", str(tmp_path))
    config = tmp_path / "sources.yaml"
    config.write_text(
        "sources:\n  steam_reviews:\n    enabled: true\n    min_interval_s: 0\n    appids: [42]\n",
        encoding="utf-8",
    )
    pages = {
        "*": {"reviews": [_review(2, 200)], "cursor": "next"},
        "next": {"reviews": [_review(1, 100)]},
    }
    calls = []
    monkeypatch.setattr("whitespace.cli._client", lambda settings: _token_client(pages, calls))
    args = ["--config", str(config), "ingest", "steam_reviews", "--max-pages", "1"]
    assert main([*args, "--backfill"]) == 0
    assert "rerun ingest to resume" in capsys.readouterr().out
    assert main(args) == 0
    assert "complete=True" in capsys.readouterr().out
    db = Database(tmp_path / "whitespace.sqlite")
    assert db.count("post") == 2 and db.get_cursor("steam_reviews", "42") == 200
    assert calls == ["*", "next"]
