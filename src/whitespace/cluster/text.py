"""Text that gets embedded for clustering: the want, prefixed with its genre context."""

from __future__ import annotations

import json

from whitespace.storage import Database


def context_text(summary: str, genres: list[str] | None, max_genres: int = 3) -> str:
    """'roguelike, action: faster progression system'. Genre first so the embedding separates
    the same want in different genres; with no genres the summary is used unchanged."""
    g = [x.strip().lower() for x in (genres or []) if x and x.strip()][:max_genres]
    return f"{', '.join(g)}: {summary}" if g else summary


def load_cluster_inputs(db: Database, min_confidence: float = 0.6) -> list[tuple[int, str]]:
    """Wants worth clustering: confident enough (junk-prone items are scored lower)."""
    rows = db.conn.execute(
        "SELECT id, want_summary, context_genres FROM want_item "
        "WHERE COALESCE(confidence, 1.0) >= ? AND COALESCE(request_type, '') != 'dislike' "
        "ORDER BY id",
        (min_confidence,),
    ).fetchall()
    return [
        (
            r["id"],
            context_text(
                r["want_summary"], json.loads(r["context_genres"]) if r["context_genres"] else None
            ),
        )
        for r in rows
    ]
