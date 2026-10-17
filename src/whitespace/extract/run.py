from __future__ import annotations

import json
from collections.abc import Callable

from whitespace.extract.nlp import EXTRACTOR_VERSION
from whitespace.extract.prefilter import is_candidate
from whitespace.extract.schema import WantItem
from whitespace.storage import Database

Extractor = Callable[[str], list[WantItem]]


def extract_all(db: Database, extractor: Extractor, limit: int | None = None) -> dict[str, int]:
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
        for it in extractor(row["text"]):
            db.conn.execute(
                "INSERT OR IGNORE INTO want_item(post_id, request_type, subject, want_summary, "
                "referenced_games, referenced_appids, intensity, quote, confidence, "
                "extractor_version) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    row["id"],
                    it.request_type,
                    it.subject,
                    it.want_summary,
                    json.dumps(it.referenced_games),
                    json.dumps(it.referenced_appids),
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
