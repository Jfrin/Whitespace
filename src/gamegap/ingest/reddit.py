"""Reddit via the official OAuth API. Re-read the current Data API terms before enabling."""

from __future__ import annotations

import json
from collections.abc import Iterator

import httpx

from gamegap.ingest.base import RateLimiter, RawItem, SourceCrawler
from gamegap.models import Post, hash_author
from gamegap.storage import Database, RawStore

TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
API = "https://oauth.reddit.com"


def get_token(client: httpx.Client, client_id: str, secret: str) -> str:
    r = client.post(TOKEN_URL, auth=(client_id, secret), data={"grant_type": "client_credentials"})
    r.raise_for_status()
    return r.json()["access_token"]


class RedditCrawler(SourceCrawler):
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
    ) -> None:
        super().__init__(db, raw, limiter, scope=subreddit)
        self.subreddit, self.client = subreddit, client
        self.token, self.max_pages = token, max_pages

    def fetch_since(self, cursor: int | None) -> Iterator[RawItem]:
        after: str | None = None
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
                if cursor is not None and ts <= cursor:
                    return
                yield RawItem(
                    key=d["name"],
                    content=json.dumps(d),
                    ext="json",
                    url=f"https://www.reddit.com{d['permalink']}",
                    cursor_value=ts,
                )
            after = data.get("after")
            if not after:
                return

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
