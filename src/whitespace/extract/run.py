from __future__ import annotations

import json
import sys
from collections.abc import Callable

from whitespace.extract.errors import ExtractorStop
from whitespace.extract.nlp import EXTRACTOR_VERSION
from whitespace.extract.prefilter import is_any_candidate
from whitespace.extract.rules import is_english
from whitespace.extract.schema import WantItem
from whitespace.storage import Database

Extractor = Callable[[str], list[WantItem]]


def extract_all(
    db: Database,
    extractor: Extractor,
    limit: int | None = None,
    redo: bool = False,
    prefilter: str = "cue",
) -> dict[str, int]:
    """`redo` deletes every want_item first (posts are untouched) so new rules re-run on all.
    `prefilter`: "cue" skips posts with no wish trigger or lack cue; "all" sends every English
    post (for an LLM, which can find implicit demand the regexes can't)."""
    if redo:
        db.conn.execute("DELETE FROM want_item")
        db.conn.commit()
    version = getattr(extractor, "version", EXTRACTOR_VERSION)
    rows = db.conn.execute(
        "SELECT p.* FROM post p LEFT JOIN want_item w ON w.post_id = p.id "
        "WHERE w.id IS NULL AND json_extract(p.flags,'$.is_dup') = 0 "
        "AND json_extract(p.flags,'$.is_low_effort') = 0"
    ).fetchall()
    stats = {"scanned": 0, "candidates": 0, "items": 0, "non_english": 0}
    for row in rows:
        stats["scanned"] += 1
        if not is_english(row["lang"]):
            stats["non_english"] += 1  # English only for now; see PLAN.md
            continue
        if prefilter != "all" and not is_any_candidate(row["text"] or ""):
            continue
        try:
            found = extractor(row["text"])
        except ExtractorStop as exc:
            stats["stopped"] = 1
            print(f"stopped early: {exc}", file=sys.stderr)
            break
        stats["candidates"] += 1
        for it in found:
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
                    version,
                ),
            )
            stats["items"] += 1
        if stats["candidates"] % 25 == 0:
            db.conn.commit()  # keep progress if the run is interrupted
        if limit and stats["candidates"] >= limit:
            break
    db.conn.commit()
    return stats
