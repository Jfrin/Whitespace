"""Attach context to every extracted want: which game, which genres, and what scope.

Runs as a separate pass after `extract` and `catalog`, and is safe to re-run: rows whose genres
couldn't be resolved yet (catalog not fetched) are retried on the next run.
"""

from __future__ import annotations

import json
from collections.abc import Iterable

from whitespace.extract.rules import classify_scope
from whitespace.storage import Database

# Steam genres that don't describe a niche ("Indie" is on a huge share of the catalog; Gore and
# Nudity are content flags; the rest are non-game software categories).
NON_NICHE_GENRES = {
    "indie",
    "early access",
    "free to play",
    "gore",
    "violent",
    "nudity",
    "sexual content",
    "utilities",
    "animation & modeling",
    "design & illustration",
    "photo editing",
    "video production",
    "audio production",
    "web publishing",
    "software training",
    "education",
    "game development",
    "accounting",
}


def niche_genres(genres: list[str]) -> list[str]:
    return [g for g in genres if g.strip().lower() not in NON_NICHE_GENRES]


def source_context(source: str, meta_json: str | None) -> str | None:
    """Where a post lives when it isn't a per-game source, e.g. 'reddit:roguelikes'."""
    if source == "steam_reviews":
        return None
    meta = json.loads(meta_json or "{}")
    if source == "reddit" and meta.get("subreddit"):
        return f"reddit:{meta['subreddit']}"
    return source


def _union(lists: Iterable[list[str]]) -> list[str]:
    seen: list[str] = []
    for lst in lists:
        for g in lst:
            if g not in seen:
                seen.append(g)
    return seen


def resolve_genres(
    db: Database,
    scope: str,
    game_appid: int | None,
    referenced_appids: list[int],
    src_ctx: str | None,
    source_genres: dict[str, list[str]],
) -> list[str]:
    """Best available genre list. For 'like Y' wants the referenced games define the niche, so
    their genres come first; otherwise the game the post is about wins."""
    ref = niche_genres(_union(db.catalog_genres(a) for a in referenced_appids))
    game = niche_genres(db.catalog_genres(game_appid)) if game_appid is not None else []
    mapped = list(source_genres.get(src_ctx or "", []))
    order = [ref, game, mapped] if scope == "reference" else [game, mapped, ref]
    for candidate in order:
        if candidate:
            return candidate
    return []


def enrich_context(
    db: Database,
    source_genres: dict[str, list[str]] | None = None,
    refresh: bool = False,
) -> dict[str, int]:
    """`refresh` recomputes every row (scope and genres), e.g. after changing the rules."""
    source_genres = source_genres or {}
    where = "" if refresh else "WHERE w.scope IS NULL OR w.context_genres IS NULL"
    rows = db.conn.execute(
        "SELECT w.id, w.request_type, w.quote, w.referenced_appids, w.scope, "
        "p.game_appid, p.source, p.meta "
        f"FROM want_item w JOIN post p ON p.id = w.post_id {where}"
    ).fetchall()
    stats = {"rows": len(rows), "with_genres": 0, "unresolved": 0}
    for r in rows:
        game_appid = r["game_appid"]
        scope = (not refresh and r["scope"]) or classify_scope(
            r["quote"] or "", r["request_type"] or "", game_appid is not None
        )
        src_ctx = source_context(r["source"], r["meta"])
        referenced = [a for a in json.loads(r["referenced_appids"] or "[]") if isinstance(a, int)]
        genres = resolve_genres(db, scope, game_appid, referenced, src_ctx, source_genres)
        keep_old = "NULL" if refresh else "context_genres"
        db.conn.execute(
            "UPDATE want_item SET scope=?, context_appid=?, source_context=?, "
            f"context_genres=COALESCE(?, {keep_old}) WHERE id=?",
            (scope, game_appid, src_ctx, json.dumps(genres) if genres else None, r["id"]),
        )
        if genres:
            stats["with_genres"] += 1
        else:
            stats["unresolved"] += 1
    db.conn.commit()
    return stats
