"""Game catalog: genres + categories per Steam appid, from the public store `appdetails` endpoint.

Gives every want a genre context. Results are cached (`max_age_days`) and rate limited. Steam
throttles this endpoint hard (HTTP 429), so a 429 stops the run and the next run resumes where it
left off. Genres are coarse (Indie, RPG, Simulation); user-voted tags would be finer-grained but
come from a third party, so they are not used here.
"""

from __future__ import annotations

import json

import httpx

from whitespace.ingest.base import RateLimiter
from whitespace.models import CatalogGame
from whitespace.storage import Database, RawStore

APPDETAILS = "https://store.steampowered.com/api/appdetails"


def parse_appdetails(appid: int, payload: dict) -> CatalogGame | None:
    """None when Steam reports no data for the appid (removed, region-locked, not a game)."""
    if not isinstance(payload, dict):
        return None
    entry = payload.get(str(appid)) or {}
    if not entry.get("success"):
        return None
    data = entry.get("data") or {}
    price_overview = data.get("price_overview") or {}
    price: float | None = None
    if data.get("is_free"):
        price = 0.0
    elif price_overview.get("final") is not None:
        price = price_overview["final"] / 100
    return CatalogGame(
        appid=appid,
        name=data.get("name"),
        genres=[g["description"] for g in data.get("genres") or [] if g.get("description")],
        tags=[c["description"] for c in data.get("categories") or [] if c.get("description")],
        release_date=(data.get("release_date") or {}).get("date"),
        price=price,
    )


def referenced_appids(db: Database) -> list[int]:
    """Every appid we need genres for: games wants were said about + games they reference."""
    rows = db.conn.execute(
        "SELECT DISTINCT p.game_appid FROM post p JOIN want_item w ON w.post_id = p.id "
        "WHERE p.game_appid IS NOT NULL "
        "UNION "
        "SELECT DISTINCT CAST(j.value AS INTEGER) FROM want_item w, "
        "json_each(w.referenced_appids) j WHERE CAST(j.value AS INTEGER) > 0"
    ).fetchall()
    return sorted({r[0] for r in rows})


def refresh_catalog(
    db: Database,
    client: httpx.Client,
    limiter: RateLimiter,
    appids: list[int],
    raw: RawStore | None = None,
    max_age_days: int = 30,
    limit: int | None = None,
) -> dict[str, int]:
    stats = {"fetched": 0, "fresh": 0, "missing": 0, "failed": 0, "rate_limited": 0}
    for appid in appids:
        if db.catalog_is_fresh(appid, max_age_days):
            stats["fresh"] += 1
            continue
        if limit is not None and stats["fetched"] + stats["missing"] >= limit:
            break
        limiter.wait()
        try:
            resp = client.get(APPDETAILS, params={"appids": appid, "cc": "us", "l": "english"})
            resp.raise_for_status()
            payload = resp.json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                stats["rate_limited"] += 1
                break
            stats["failed"] += 1
            continue
        except (httpx.HTTPError, ValueError):
            stats["failed"] += 1
            continue
        if raw is not None:
            raw.save("steam_appdetails", str(appid), json.dumps(payload), "json")
        game = parse_appdetails(appid, payload)
        if game is None:
            # remember the miss so we don't re-request it every run; it expires like any entry
            db.upsert_catalog(CatalogGame(appid=appid, name=None))
            stats["missing"] += 1
        else:
            db.upsert_catalog(game)
            stats["fetched"] += 1
    return stats
