from whitespace.ingest.forum import ForumCrawler, parse_posts

SEL = {
    "post": ".message",
    "post_id_attr": "data-id",
    "author": ".author::text",
    "body": ".message-body::text",
    "timestamp_attr": "time::attr(datetime)",
}


def test_parse_posts_extracts_and_skips_incomplete(forum_html):
    posts = parse_posts(forum_html, SEL, "forum", "http://f/t")
    assert [p.source_id for p in posts] == ["101", "102"]
    assert "cozy farming sim" in posts[0].text
    assert posts[0].url == "http://f/t#post-101" and posts[0].author_hash


def test_forum_crawler_is_incremental(db, raw, limiter, forum_html):
    opts = {"start_urls": ["http://f/t"], "selectors": SEL}
    crawler = lambda: ForumCrawler(db, raw, limiter, "forum", opts, fetch=lambda u: forum_html)  # noqa: E731
    assert crawler().run().fetched == 1 and db.count("post") == 2
    assert crawler().run().fetched == 0  # nothing newer than cursor
    assert db.count("raw_fetch") == 1


def test_forum_sees_changed_page_at_same_timestamp(db, raw, limiter, forum_html):
    opts = {"start_urls": ["http://f/t"], "selectors": SEL}
    ForumCrawler(db, raw, limiter, "forum", opts, fetch=lambda _: forum_html).run()
    # Replace the oldest post with a new ID at the newest post's timestamp.
    import re

    timestamps = re.findall(r'datetime="([^"]+)"', forum_html)
    updated = forum_html.replace('data-id="101"', 'data-id="103"').replace(
        timestamps[0], timestamps[1], 1
    )
    stats = ForumCrawler(db, raw, limiter, "forum", opts, fetch=lambda _: updated).run()
    assert stats.fetched == 1
    assert db.conn.execute("SELECT 1 FROM post WHERE source_id='103'").fetchone()
