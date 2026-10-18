"""Text that gets embedded for clustering: the want, prefixed with its genre context."""

from __future__ import annotations

import json

from whitespace.storage import Database


def context_text(summary: str, genres: list[str] | None, max_genres: int = 3) -> str:
    """'roguelike, action: faster progression system'. Genre first so the embedding separates
    the same want in different genres; with no genres the summary is used unchanged."""
    g = [x.strip().lower() for x in (genres or []) if x and x.strip()][:max_genres]
    return f"{', '.join(g)}: {summary}" if g else summary


def load_cluster_inputs(db: Database) -> list[tuple[int, str]]:
    rows = db.conn.execute(
        "SELECT id, want_summary, context_genres FROM want_item ORDER BY id"
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
