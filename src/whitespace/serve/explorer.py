"""Queries for browsing extracted items in the local dashboard."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from urllib.parse import urlparse


@dataclass(frozen=True)
class Filters:
    games: list[int] = field(default_factory=list)
    genres: list[str] = field(default_factory=list)
    subjects: list[str] = field(default_factory=list)
    request_types: list[str] = field(default_factory=list)
    search: str = ""
    min_confidence: float = 0.0


FROM = (
    " FROM want_item w JOIN post p ON p.id = w.post_id "
    "LEFT JOIN catalog_game c ON c.appid = COALESCE(w.context_appid, p.game_appid) "
)
SAFE_GENRES = (
    "CASE WHEN json_valid(w.context_genres) THEN "
    "CASE WHEN json_type(w.context_genres) = 'array' THEN w.context_genres ELSE '[]' END "
    "ELSE '[]' END"
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


def options(conn: sqlite3.Connection) -> dict:
    games = conn.execute(
        "SELECT DISTINCT COALESCE(w.context_appid, p.game_appid) AS appid, c.name"
        + FROM
        + "WHERE COALESCE(w.context_appid, p.game_appid) IS NOT NULL ORDER BY c.name, appid"
    ).fetchall()
    genres = {
        genre
        for row in conn.execute("SELECT DISTINCT context_genres FROM want_item")
        for genre in string_list(row[0])
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
        "games": {
            row["appid"]: f"{row['name'] or 'Unknown game'} ({row['appid']})" for row in games
        },
        "genres": sorted(genres, key=str.casefold),
        "subjects": distinct("subject"),
        "request_types": distinct("request_type"),
    }


def _where(filters: Filters) -> tuple[str, list]:
    clauses: list[str] = []
    params: list = []
    for column, values in (
        ("COALESCE(w.context_appid, p.game_appid)", filters.games),
        ("w.subject", filters.subjects),
        ("w.request_type", filters.request_types),
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
    where, params = _where(filters)
    sql = (
        "SELECT w.id, w.want_summary, w.request_type, w.subject, w.quote, w.confidence, "
        "w.context_genres, w.referenced_games, w.extractor_version, w.scope, p.lang, p.source, "
        "p.url, COALESCE(w.context_appid, p.game_appid) AS game_appid, c.name AS game_name"
        + FROM
        + where
        + " ORDER BY w.id DESC LIMIT ? OFFSET ?"
    )
    return [
        dict(row) for row in conn.execute(sql, [*params, page_size, (page_number - 1) * page_size])
    ]
