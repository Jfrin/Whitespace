"""Generic HTML forum crawler built on Scrapling.

Parsing (`parse_posts`) only needs Scrapling's parser, so it is unit-testable offline.
Fetching uses `scrapling.fetchers.Fetcher` (install the `scrape` extra). For JS-heavy sites swap
in `DynamicFetcher`; use `StealthyFetcher` only where the site's ToS permits automated access.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import datetime
from typing import Any

from scrapling.parser import Selector

from whitespace.ingest.base import RateLimiter, RawItem, SourceCrawler
from whitespace.models import Post, hash_author, sha1
from whitespace.storage import Database, RawStore


def _iso_to_epoch(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def parse_posts(html: str, selectors: dict[str, str], source: str, page_url: str) -> list[Post]:
    page = Selector(html)
    posts: list[Post] = []
    for node in page.css(selectors["post"]):
        body = " ".join(t.strip() for t in node.css(selectors["body"]).getall() if t.strip())
        pid = node.attrib.get(selectors.get("post_id_attr", "id"))
        ts = node.css(selectors["timestamp_attr"]).get()
        if not (body and pid and ts):
            continue
        posts.append(
            Post(
                source=source,
                source_id=str(pid),
                url=f"{page_url}#post-{pid}",
                text=body,
                created_at=_iso_to_epoch(ts),
                author_hash=hash_author(node.css(selectors["author"]).get()),
            )
        )
    return posts


def default_fetch(url: str) -> str:
    from scrapling.fetchers import Fetcher  # lazy: needs the `scrape` extra

    page = Fetcher.get(url, stealthy_headers=True)
    return page.html_content


class ForumCrawler(SourceCrawler):
    parser_version = "1"

    def __init__(
        self,
        db: Database,
        raw: RawStore,
        limiter: RateLimiter,
        name: str,
        options: dict[str, Any],
        fetch: Callable[[str], str] = default_fetch,
    ) -> None:
        self.name = name
        super().__init__(db, raw, limiter, scope="all")
        self.options, self.fetch = options, fetch

    def fetch_since(self, cursor: int | None) -> Iterator[RawItem]:
        for url in self.options["start_urls"]:
            self.limiter.wait()
            html = self.fetch(url)
            posts = parse_posts(html, self.options["selectors"], self.name, url)
            fresh = [p for p in posts if cursor is None or p.created_at > cursor]
            if not fresh:
                continue
            # key includes the newest post id so unchanged pages are not re-stored
            newest = max(fresh, key=lambda p: p.created_at)
            yield RawItem(
                key=f"{sha1(url)[:12]}-{newest.source_id}",
                content=html,
                ext="html",
                url=url,
                cursor_value=newest.created_at,
            )

    def parse(self, item: RawItem) -> list[Post]:
        html = item.content.decode() if isinstance(item.content, bytes) else item.content
        return parse_posts(html, self.options["selectors"], self.name, item.url)
