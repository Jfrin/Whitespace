import httpx

from whitespace.ingest.reddit import RedditCrawler, get_token


def _post(i, ts):
    return {
        "name": f"t3_{i}",
        "created_utc": ts,
        "title": "Games like Stardew?",
        "selftext": "I wish one had automation.",
        "author": "someone",
        "permalink": f"/r/x/comments/{i}/",
        "subreddit": "x",
        "score": 5,
    }


def test_token_and_incremental_listing(db, raw, limiter):
    seen_auth = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "www.reddit.com":
            return httpx.Response(200, json={"access_token": "tok"})
        seen_auth.append(req.headers["authorization"])
        kids = [{"data": _post(2, 200)}, {"data": _post(1, 100)}]
        return httpx.Response(200, json={"data": {"children": kids, "after": None}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    tok = get_token(client, "id", "secret")
    assert tok == "tok"
    s1 = RedditCrawler(db, raw, limiter, "x", client, tok).run()
    s2 = RedditCrawler(db, raw, limiter, "x", client, tok).run()
    assert s1.fetched == 2 and s2.fetched == 0  # second run stops at the cursor
    assert seen_auth[0] == "bearer tok" and db.get_cursor("reddit", "x") == 200
    assert "Games like Stardew?" in db.conn.execute("SELECT text FROM post").fetchone()["text"]


def test_reddit_resumes_pages_and_keeps_timestamp_ties(db, raw, limiter):
    db.set_cursor("reddit", "x", 100)
    calls = []

    def handler(req):
        after = req.url.params.get("after") or None
        calls.append(after)
        posts = [_post(3, 200), _post(2, 100)] if after is None else [_post(1, 100), _post(0, 99)]
        return httpx.Response(
            200,
            json={
                "data": {
                    "children": [{"data": post} for post in posts],
                    "after": "next" if after is None else None,
                }
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert not RedditCrawler(db, raw, limiter, "x", client, "token", max_pages=1).run().complete
    assert db.get_cursor("reddit", "x") == 100
    assert RedditCrawler(db, raw, limiter, "x", client, "token", max_pages=1).run().complete
    assert calls == [None, "next"] and db.get_cursor("reddit", "x") == 200
    assert db.count("post") == 3
