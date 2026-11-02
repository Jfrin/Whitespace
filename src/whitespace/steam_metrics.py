"""Steam review engagement: hydrate stored raw reviews or refresh existing reviews."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import httpx

from whitespace.ingest.base import RateLimiter
from whitespace.ingest.steam_review_fields import review_metrics
from whitespace.ingest.steam_reviews import URL
from whitespace.storage import Database, now_iso


def _update(db: Database, appid: int, review: dict, observed_at: str) -> bool:
    review_id = review.get("recommendationid")
    metrics = review_metrics(review)
    if not metrics or not isinstance(review_id, (int, str)) or isinstance(review_id, bool):
        return False
    row = db.conn.execute(
        "SELECT id, meta FROM post WHERE source='steam_reviews' AND source_id=? AND game_appid=?",
        (f"{appid}-{review_id}", appid),
    ).fetchone()
    if not row:
        return False
    try:
        observed = datetime.fromisoformat(observed_at)
        meta = json.loads(row["meta"] or "{}")
        if not isinstance(meta, dict):
            return False
        previous = meta.get("review_metrics_observed_at")
        if previous and datetime.fromisoformat(previous) > observed:
            return False
    except (ValueError, TypeError):
        return False
    meta.update(metrics)
    meta["review_metrics_observed_at"] = observed_at
    db.conn.execute("UPDATE post SET meta=? WHERE id=?", (json.dumps(meta), row["id"]))
    return True


def hydrate_review_metrics(db: Database) -> dict[str, int]:
    """Recover previously captured metrics without making network requests."""
    stats = {"scanned": 0, "updated": 0, "unavailable": 0}
    rows = db.conn.execute(
        "SELECT r.id, r.path, r.pending_content, r.fetched_at, p.game_appid, p.source_id "
        "FROM raw_fetch r "
        "JOIN post p ON p.source=r.source AND p.source_id=r.key "
        "WHERE r.source='steam_reviews' AND p.game_appid IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM steam_review_metric_hydration h WHERE h.raw_id=r.id) "
        "ORDER BY r.id"
    )
    try:
        for row in rows:
            stats["scanned"] += 1
            try:
                content = row["pending_content"] or Path(row["path"]).read_bytes()
                payload = json.loads(content)
                if (
                    not isinstance(payload, dict)
                    or f"{row['game_appid']}-{payload.get('recommendationid')}" != row["source_id"]
                ):
                    raise ValueError("invalid review")
                stats["updated"] += int(_update(db, row["game_appid"], payload, row["fetched_at"]))
                db.conn.execute(
                    "INSERT INTO steam_review_metric_hydration(raw_id) VALUES (?)", (row["id"],)
                )
            except (OSError, ValueError, TypeError):
                stats["unavailable"] += 1
            if stats["scanned"] % 128 == 0:
                db.conn.commit()
    except BaseException:
        db.conn.rollback()
        raise
    else:
        db.conn.commit()
    return stats


def refresh_review_metrics(
    db: Database, client: httpx.Client, limiter: RateLimiter, appids: list[int], max_pages: int = 20
) -> dict[str, int]:
    """Refresh existing reviews only, checkpointing pages separately from ingestion."""
    if max_pages < 1:
        raise ValueError("max_pages must be positive")
    stats = {"pages": 0, "updated": 0, "partial": 0, "failed": 0, "rate_limited": 0}
    for appid in appids:
        state = db.conn.execute(
            "SELECT token FROM steam_review_metric_cursor WHERE appid=?", (appid,)
        ).fetchone()
        token = state["token"] if state else "*"
        completed = False
        for _ in range(max_pages):
            limiter.wait()
            try:
                response = client.get(
                    URL,
                    params={
                        "input_json": json.dumps(
                            {
                                "appid": appid,
                                "filter": 1,
                                "languages": ["all"],
                                "review_type": 0,
                                "purchase_type": 1,
                                "num_per_page": 100,
                                "cursor": token,
                            }
                        )
                    },
                )
                if response.status_code == 429:
                    stats["rate_limited"] += 1
                    return stats
                response.raise_for_status()
                payload = response.json()
                data = payload.get("response") if isinstance(payload, dict) else None
                if not isinstance(data, dict) or not isinstance(data.get("reviews"), list):
                    raise ValueError("Invalid review response")
                reviews = data.get("reviews", [])
                next_token = data.get("cursor")
                if (
                    reviews
                    and next_token
                    and (not isinstance(next_token, str) or next_token == token)
                ):
                    raise ValueError("Invalid or repeated review cursor")
                with db.conn:
                    stamp = now_iso()
                    for review in reviews:
                        if isinstance(review, dict):
                            stats["updated"] += int(_update(db, appid, review, stamp))
                    if reviews and next_token:
                        db.conn.execute(
                            "INSERT INTO steam_review_metric_cursor(appid, token) VALUES (?,?) "
                            "ON CONFLICT(appid) DO UPDATE SET token=excluded.token",
                            (appid, next_token),
                        )
                    else:
                        db.conn.execute(
                            "DELETE FROM steam_review_metric_cursor WHERE appid=?", (appid,)
                        )
                stats["pages"] += 1
                if not reviews or not next_token:
                    completed = True
                    break
                token = next_token
            except (httpx.HTTPError, ValueError, TypeError):
                stats["failed"] += 1
                break
        if not completed:
            stats["partial"] += 1
    return stats
