import json

import httpx
import pytest

from whitespace.ingest.steam_reviews import SteamReviewsCrawler


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
