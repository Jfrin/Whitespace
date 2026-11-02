"""Reddit via the official OAuth API. Re-read the current Data API terms before enabling."""

from __future__ import annotations

import json
from collections.abc import Iterator

import httpx

from whitespace.ingest.base import PaginatedCrawler, RateLimiter, RawItem
from whitespace.models import Post, hash_author
from whitespace.storage import Database, RawStore

TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
API = "https://oauth.reddit.com"


def get_token(client: httpx.Client, client_id: str, secret: str) -> str:
    r = client.post(TOKEN_URL, auth=(client_id, secret), data={"grant_type": "client_credentials"})
    r.raise_for_status()
    return r.json()["access_token"]


class RedditCrawler(PaginatedCrawler):
    name = "reddit"

    def __init__(
        self,
        db: Database,
        raw: RawStore,
        limiter: RateLimiter,
        subreddit: str,
        client: httpx.Client,
        token: str,
        max_pages: int = 10,
        backfill: bool = False,
    ) -> None:
        if max_pages < 1:
            raise ValueError("max_pages must be positive")
        super().__init__(db, raw, limiter, scope=subreddit, backfill=backfill)
        self.subreddit, self.client = subreddit, client
        self.token, self.max_pages = token, max_pages

    def fetch_since(self, cursor: int | None) -> Iterator[RawItem]:
        after, cutoff = self.begin_pages(cursor, None)
        for _ in range(self.max_pages):
            self.limiter.wait()
            resp = self.client.get(
                f"{API}/r/{self.subreddit}/new",
                params={"limit": 100, "after": after, "raw_json": 1},
                headers={"Authorization": f"bearer {self.token}"},
            )
            resp.raise_for_status()
            data = resp.json()["data"]
            for child in data["children"]:
                d = child["data"]
                ts = int(d["created_utc"])
                if cutoff is not None and ts < cutoff:
                    self._page_complete = True
                    return
                self._page_newest = max(self._page_newest, ts)
                yield RawItem(
                    key=d["name"],
                    content=json.dumps(d),
                    ext="json",
                    url=f"https://www.reddit.com{d['permalink']}",
                    cursor_value=ts,
                )
            next_after = data.get("after")
            if not next_after:
                self._page_complete = True
                return
            if not isinstance(next_after, str) or next_after == after:
                raise ValueError("Reddit API returned an invalid or repeated cursor")
            self.checkpoint_page(next_after)
            after = next_after

    def parse(self, item: RawItem) -> list[Post]:
        d = json.loads(item.content)
        text = f"{d.get('title', '')}\n\n{d.get('selftext', '')}".strip()
        if not text:
            return []
        return [
            Post(
                source=self.name,
                source_id=d["name"],
                url=item.url,
                text=text,
                created_at=int(d["created_utc"]),
                author_hash=hash_author(d.get("author")),
                meta={"subreddit": d.get("subreddit"), "score": d.get("score")},
            )
        ]
