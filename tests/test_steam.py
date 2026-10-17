import httpx

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
        calls.append(dict(request.url.params))
        page = pages[len(calls) - 1] if len(calls) <= len(pages) else {"reviews": []}
        return httpx.Response(200, json=page)

    return httpx.Client(transport=httpx.MockTransport(handler))


PAGES = [
    {"reviews": [_review(3, 300), _review(2, 200)], "cursor": "c1"},
    {"reviews": [_review(1, 100)], "cursor": "c2"},
    {"reviews": [], "cursor": "c3"},
]


def test_full_crawl_paginates_and_stores(db, raw, limiter):
    calls: list = []
    stats = SteamReviewsCrawler(db, raw, limiter, 42, _client(PAGES, calls)).run()
    assert stats.fetched == 3 and stats.new_posts == 3
    assert calls[1]["cursor"] == "c1"  # cursor threaded through
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
