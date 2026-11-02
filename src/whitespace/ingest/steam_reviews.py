"""Steam reviews via the official IUserReviewsService Web API."""

from __future__ import annotations

import json
from collections.abc import Iterator

import httpx

from whitespace.ingest.base import PaginatedCrawler, RateLimiter, RawItem
from whitespace.ingest.steam_review_fields import review_metrics
from whitespace.models import Post, hash_author
from whitespace.storage import Database, RawStore

URL = "https://api.steampowered.com/IUserReviewsService/GetAppReviews/v1/"
REVIEW_TYPES = {"all": 0, "positive": 1, "negative": 2}


class SteamReviewsCrawler(PaginatedCrawler):
    name = "steam_reviews"

    def __init__(
        self,
        db: Database,
        raw: RawStore,
        limiter: RateLimiter,
        appid: int,
        client: httpx.Client,
        review_type: str = "negative",  # negative reviews are rich in "I wish it had X"
        max_pages: int = 20,
        backfill: bool = False,
    ) -> None:
        self.appid, self.client = appid, client
        if review_type not in REVIEW_TYPES:
            raise ValueError(f"Unknown review type: {review_type!r}")
        if max_pages < 1:
            raise ValueError("max_pages must be positive")
        super().__init__(db, raw, limiter, scope=str(appid), backfill=backfill, variant=review_type)
        self.review_type, self.max_pages = review_type, max_pages

    def fetch_since(self, cursor: int | None) -> Iterator[RawItem]:
        token, cutoff = self.begin_pages(cursor, "*")
        for _ in range(self.max_pages):
            self.limiter.wait()
            resp = self.client.get(
                URL,
                params={
                    "input_json": json.dumps(
                        {
                            "appid": self.appid,
                            "filter": 1,  # recent, newest first
                            "languages": ["all"],
                            "review_type": REVIEW_TYPES[self.review_type],
                            "purchase_type": 1,  # all purchases
                            "num_per_page": 100,
                            "cursor": token,
                        }
                    )
                },
            )
            resp.raise_for_status()
            payload = resp.json()
            data = payload.get("response") if isinstance(payload, dict) else None
            if not isinstance(data, dict):
                raise ValueError("Steam reviews API returned a malformed response")
            reviews = data.get("reviews", [])
            if not isinstance(reviews, list):
                raise ValueError("Steam reviews API returned invalid reviews")
            if not reviews:
                self._page_complete = True
                return
            for rev in reviews:
                ts = int(rev["timestamp_created"])
                if cutoff is not None and ts < cutoff:
                    self._page_complete = True
                    return  # `recent` is newest-first, so everything after is old
                self._page_newest = max(self._page_newest, ts)
                rev["_appid"] = self.appid
                yield RawItem(
                    key=f"{self.appid}-{rev['recommendationid']}",
                    content=json.dumps(rev),
                    ext="json",
                    url=f"https://steamcommunity.com/app/{self.appid}/reviews/",
                    cursor_value=ts,
                )
            next_token = data.get("cursor")
            if not next_token:
                self._page_complete = True
                return
            if not isinstance(next_token, str) or next_token == token:
                raise ValueError("Steam reviews API returned an invalid or repeated cursor")
            self.checkpoint_page(next_token)
            token = next_token

    def parse(self, item: RawItem) -> list[Post]:
        rev = json.loads(item.content)
        text = (rev.get("review") or "").strip()
        if not text:
            return []
        return [
            Post(
                source=self.name,
                source_id=f"{rev['_appid']}-{rev['recommendationid']}",
                url=item.url,
                text=text,
                created_at=int(rev["timestamp_created"]),
                author_hash=hash_author(str(rev.get("author", {}).get("steamid"))),
                lang=rev.get("language"),
                game_appid=int(rev["_appid"]),
                meta=review_metrics(rev),
            )
        ]
