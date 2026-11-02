"""Public Steam store user-defined tags, separate from appdetails categories."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
from scrapling.parser import Selector

from whitespace.ingest.base import RateLimiter
from whitespace.storage import Database, now_iso

URL = "https://store.steampowered.com/app/{appid}/"


class RestrictedStorePage(ValueError):
    """Steam requires an age check or login; never bypass it."""


def parse_user_tags(html: str, appid: int) -> list[str]:
    page = Selector(html)
    if page.css('#agegate_box, .age_gate, form[action*="/agecheck/"]'):
        raise RestrictedStorePage("Steam store page requires an age check")
    containers = page.css(f'div.glance_tags.popular_tags[data-appid="{appid}"]')
    if not containers:
        raise ValueError("Steam tag section unavailable or page layout changed")
    seen: set[str] = set()
    tags = []
    for value in containers[0].css("a.app_tag::text").getall():
        name = " ".join(value.split())
        if name and name.casefold() not in seen:
            tags.append(name)
            seen.add(name.casefold())
    return tags


def refresh_user_tags(
    db: Database,
    client: httpx.Client,
    limiter: RateLimiter,
    appids: list[int],
    max_age_days: int = 30,
    limit: int | None = None,
) -> dict[str, int]:
    if max_age_days < 0 or (limit is not None and limit < 1):
        raise ValueError("max age must be non-negative and limit must be positive")
    stats = {"fetched": 0, "fresh": 0, "restricted": 0, "failed": 0, "rate_limited": 0}
    attempted = 0
    for appid in appids:
        row = db.conn.execute(
            "SELECT fetched_at FROM steam_user_tags WHERE appid=?", (appid,)
        ).fetchone()
        try:
            fresh = (
                row
                and row["fetched_at"]
                and (
                    datetime.now(UTC) - datetime.fromisoformat(row["fetched_at"])
                    < timedelta(days=max_age_days)
                )
            )
        except (ValueError, TypeError):
            fresh = False
        if fresh:
            stats["fresh"] += 1
            continue
        if limit is not None and attempted >= limit:
            break
        attempted += 1
        limiter.wait()
        status = "failed"
        tags = None
        try:
            response = client.get(
                URL.format(appid=appid), params={"l": "english"}, follow_redirects=False
            )
            if response.status_code == 429:
                stats["rate_limited"] += 1
                break
            if 300 <= response.status_code < 400 or response.status_code in {401, 403}:
                status = "restricted"
            else:
                response.raise_for_status()
                tags = parse_user_tags(response.text, appid)
                status = "ok"
        except RestrictedStorePage:
            status = "restricted"
        except (httpx.HTTPError, ValueError):
            status = "failed"
        stamp = now_iso()
        db.conn.execute(
            "INSERT INTO steam_user_tags(appid,tags,fetched_at,attempted_at,status,source) "
            "VALUES (?,?,?,?,?,?) ON CONFLICT(appid) DO UPDATE SET "
            "tags=COALESCE(excluded.tags,steam_user_tags.tags), "
            "fetched_at=COALESCE(excluded.fetched_at,steam_user_tags.fetched_at), "
            "attempted_at=excluded.attempted_at,status=excluded.status,source=excluded.source",
            (
                appid,
                json.dumps(tags) if tags is not None else None,
                stamp if tags is not None else None,
                stamp,
                status,
                "steam_store_page",
            ),
        )
        db.conn.commit()
        stats[{"ok": "fetched", "restricted": "restricted", "failed": "failed"}[status]] += 1
    return stats
