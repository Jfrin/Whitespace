from __future__ import annotations

import json
from collections.abc import Callable

from gamegap.extract.llm import EXTRACTOR_VERSION, extract_wants
from gamegap.extract.prefilter import is_candidate
from gamegap.storage import Database


def extract_all(
    db: Database, call: Callable[[str], str], limit: int | None = None
) -> dict[str, int]:
    rows = db.conn.execute(
        "SELECT p.* FROM post p LEFT JOIN want_item w ON w.post_id = p.id "
        "WHERE w.id IS NULL AND json_extract(p.flags,'$.is_dup') = 0 "
        "AND json_extract(p.flags,'$.is_low_effort') = 0"
    ).fetchall()
    stats = {"scanned": 0, "candidates": 0, "items": 0}
    for row in rows:
        stats["scanned"] += 1
        if not is_candidate(row["text"] or ""):
            continue
        stats["candidates"] += 1
        for it in extract_wants(row["text"], call):
            db.conn.execute(
                "INSERT OR IGNORE INTO want_item(post_id, request_type, subject, want_summary, "
                "referenced_appids, intensity, quote, confidence, extractor_version) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    row["id"],
                    it.request_type,
                    it.subject,
                    it.want_summary,
                    json.dumps(it.referenced_games),
                    it.intensity,
                    it.quote,
                    it.confidence,
                    EXTRACTOR_VERSION,
                ),
            )
            stats["items"] += 1
        if limit and stats["candidates"] >= limit:
            break
    db.conn.commit()
    return stats
