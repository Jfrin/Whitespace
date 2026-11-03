"""Queries for browsing extracted items in the local dashboard."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from urllib.parse import urlparse

from whitespace.ingest.steam_review_fields import review_metrics


@dataclass(frozen=True)
class Filters:
    games: list[int] = field(default_factory=list)
    genres: list[str] = field(default_factory=list)
    subjects: list[str] = field(default_factory=list)
    request_types: list[str] = field(default_factory=list)
    search: str = ""
    min_confidence: float = 0.0
    user_tags: list[str] = field(default_factory=list)
    sort_by: str = "newest"
    extractor_versions: list[str] = field(default_factory=list)


FROM = (
    " FROM want_item w JOIN post p ON p.id = w.post_id "
    "LEFT JOIN catalog_game c ON c.appid = COALESCE(w.context_appid, p.game_appid) "
    "LEFT JOIN steam_user_tags t ON t.appid = COALESCE(w.context_appid, p.game_appid) "
    "LEFT JOIN raw_fetch r ON r.source=p.source AND r.key=p.source_id "
)
SAFE_GENRES = (
    "CASE WHEN json_valid(w.context_genres) THEN "
    "CASE WHEN json_type(w.context_genres) = 'array' THEN w.context_genres ELSE '[]' END "
    "ELSE '[]' END"
)
SAFE_TAGS = (
    "CASE WHEN json_valid(t.tags) THEN "
    "CASE WHEN json_type(t.tags) = 'array' THEN t.tags ELSE '[]' END ELSE '[]' END"
)


def string_list(value: str | None) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except (ValueError, TypeError):
        return []
    return (
        [s for s in parsed if isinstance(s, str) and s.strip()] if isinstance(parsed, list) else []
    )


def safe_source_url(value: str | None) -> str | None:
    try:
        url = urlparse(value or "")
        if (
            url.scheme in {"http", "https"}
            and url.hostname
            and not url.username
            and not url.password
        ):
            return value
    except ValueError:
        pass
    return None


def review_engagement(source: str, metadata: str | None) -> dict:
    if source != "steam_reviews":
        return {}
    try:
        meta = json.loads(metadata or "{}")
    except (ValueError, TypeError):
        return {}
    if not isinstance(meta, dict):
        return {}
    result = review_metrics(meta)
    if isinstance(meta.get("review_metrics_observed_at"), str):
        result["observed_at"] = meta["review_metrics_observed_at"]
    return result


def engagement_text(metrics: dict) -> list[str]:
    labels = []
    for key, adjective in (("votes_up", "helpful"), ("votes_funny", "funny")):
        if key in metrics:
            value = metrics[key]
            labels.append(
                f"{value:,} {'person' if value == 1 else 'people'} found this review {adjective}"
            )
    if "weighted_vote_score" in metrics:
        labels.append(f"Steam weighted helpfulness: {metrics['weighted_vote_score']:.3f}")
    if "voted_up" in metrics:
        labels.append("Recommended" if metrics["voted_up"] else "Not recommended")
    return labels


def options(conn: sqlite3.Connection) -> dict:
    games = conn.execute(
        "SELECT DISTINCT p.game_appid AS appid, c.name FROM post p "
        "LEFT JOIN catalog_game c ON c.appid = p.game_appid "
        "WHERE p.game_appid IS NOT NULL ORDER BY c.name, appid"
    ).fetchall()
    genres = {
        genre
        for row in conn.execute("SELECT DISTINCT context_genres FROM want_item")
        for genre in string_list(row[0])
    }
    user_tags = {
        tag
        for row in conn.execute("SELECT DISTINCT tags FROM steam_user_tags")
        for tag in string_list(row[0])
    }

    def distinct(column: str) -> list[str]:
        return [
            row[0]
            for row in conn.execute(
                f"SELECT DISTINCT {column} FROM want_item WHERE {column} IS NOT NULL "
                f"AND {column} != '' ORDER BY {column}"
            )
        ]

    return {
        "games": {row["appid"]: f"{row['name'] or 'Steam app'} ({row['appid']})" for row in games},
        "genres": sorted(genres, key=str.casefold),
        "user_tags": sorted(user_tags, key=str.casefold),
        "subjects": distinct("subject"),
        "request_types": distinct("request_type"),
        "extractor_versions": distinct("extractor_version"),
    }


def game_coverage(
    conn: sqlite3.Connection, configured_appids: list[int] | None = None
) -> list[dict]:
    """Show ingested games even when extraction has produced no wants for them yet."""
    rows = conn.execute(
        "SELECT p.game_appid AS appid, c.name, COUNT(DISTINCT p.id) AS posts, "
        "COUNT(w.id) AS wants FROM post p "
        "LEFT JOIN want_item w ON w.post_id = p.id "
        "LEFT JOIN catalog_game c ON c.appid = p.game_appid "
        "WHERE p.game_appid IS NOT NULL GROUP BY p.game_appid ORDER BY posts DESC, appid"
    ).fetchall()
    covered = {row["appid"]: dict(row) for row in rows}
    missing = [appid for appid in dict.fromkeys(configured_appids or []) if appid not in covered]
    if missing:
        placeholders = ",".join("?" for _ in missing)
        names = {
            row["appid"]: row["name"]
            for row in conn.execute(
                f"SELECT appid, name FROM catalog_game WHERE appid IN ({placeholders})", missing
            )
        }
        covered.update(
            {
                appid: {"appid": appid, "name": names.get(appid), "posts": 0, "wants": 0}
                for appid in missing
            }
        )
    return list(covered.values())


def _where(filters: Filters) -> tuple[str, list]:
    clauses: list[str] = []
    params: list = []
    for column, values in (
        ("COALESCE(w.context_appid, p.game_appid)", filters.games),
        ("w.subject", filters.subjects),
        ("w.request_type", filters.request_types),
        ("w.extractor_version", filters.extractor_versions),
    ):
        if values:
            clauses.append(f"{column} IN ({','.join('?' for _ in values)})")
            params.extend(values)
    if filters.genres:
        placeholders = ",".join("?" for _ in filters.genres)
        clauses.append(
            f"EXISTS (SELECT 1 FROM json_each({SAFE_GENRES}) g WHERE g.value IN ({placeholders}))"
        )
        params.extend(filters.genres)
    if filters.user_tags:
        placeholders = ",".join("?" for _ in filters.user_tags)
        clauses.append(
            f"EXISTS (SELECT 1 FROM json_each({SAFE_TAGS}) g WHERE g.value IN ({placeholders}))"
        )
        params.extend(filters.user_tags)
    if filters.search.strip():
        clauses.append(
            "(instr(lower(COALESCE(w.want_summary,'')), lower(?)) > 0 "
            "OR instr(lower(COALESCE(w.quote,'')), lower(?)) > 0)"
        )
        params.extend([filters.search.strip()] * 2)
    if filters.min_confidence > 0:
        clauses.append("w.confidence >= ?")
        params.append(filters.min_confidence)
    return (" WHERE " + " AND ".join(clauses) if clauses else ""), params


def count(conn: sqlite3.Connection, filters: Filters) -> int:
    where, params = _where(filters)
    return conn.execute("SELECT count(*)" + FROM + where, params).fetchone()[0]


def page(
    conn: sqlite3.Connection, filters: Filters, page_number: int, page_size: int
) -> list[dict]:
    if page_number < 1 or not 1 <= page_size <= 100:
        raise ValueError("Invalid page")
    if filters.sort_by not in {"newest", "helpful", "funny"}:
        raise ValueError("Invalid sort")
    where, params = _where(filters)
    order = "w.id DESC"
    if filters.sort_by != "newest":
        field = "votes_up" if filters.sort_by == "helpful" else "votes_funny"
        order = (
            "CASE WHEN p.source='steam_reviews' AND json_valid(p.meta) THEN "
            f"CASE WHEN json_type(p.meta,'$.{field}')='integer' "
            f"AND json_extract(p.meta,'$.{field}')>=0 "
            f"THEN json_extract(p.meta,'$.{field}') END END DESC, w.id DESC"
        )
    sql = (
        "SELECT w.id, w.want_summary, w.request_type, w.subject, w.quote, w.confidence, "
        "w.context_genres, w.referenced_games, w.extractor_version, w.scope, p.lang, p.source, "
        "p.url, p.meta AS post_meta, r.fetched_at AS raw_fetched_at, "
        "t.tags AS user_tags, t.fetched_at AS user_tags_fetched_at, "
        "COALESCE(w.context_appid, p.game_appid) AS game_appid, c.name AS game_name"
        + FROM
        + where
        + f" ORDER BY {order} LIMIT ? OFFSET ?"
    )
    return [
        dict(row) for row in conn.execute(sql, [*params, page_size, (page_number - 1) * page_size])
    ]
