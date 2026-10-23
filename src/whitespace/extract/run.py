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
    english_only: bool = True,
) -> dict[str, int]:
    """`redo` deletes every want_item first (posts are untouched) so new rules re-run on all.
    `prefilter`: "cue" skips posts with no wish trigger or lack cue; "all" sends every English
    post (for an LLM, which can find implicit demand the regexes can't).
    `english_only=False` sends every language (an LLM reads them all; the rules are English-only).
    If the extractor has `extract_batch` and `batch_size > 1`, posts are sent in groups."""
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
    work = []
    for row in rows:
        stats["scanned"] += 1
        if english_only and not is_english(row["lang"]):
            stats["non_english"] += 1  # rules extractor only handles English
            continue
        if prefilter != "all" and not is_any_candidate(row["text"] or ""):
            continue
        work.append(row)
    if limit:
        work = work[:limit]
    batcher = getattr(extractor, "extract_batch", None)
    size = int(getattr(extractor, "batch_size", 1) or 1) if batcher else 1
    print(
        f"{len(work)} posts to send ({stats['scanned']} scanned). "
        "Ctrl+C is safe: progress and API responses are saved.",
        file=sys.stderr,
    )
    try:
        for start in range(0, len(work), size):
            group = work[start : start + size]
            try:
                if batcher and size > 1:
                    results = batcher([r["text"] or "" for r in group])
                else:
                    results = [extractor(r["text"]) for r in group]
            except ExtractorStop as exc:
                stats["stopped"] = 1
                print(f"stopped early: {exc}", file=sys.stderr)
                break
            before = stats["candidates"]
            for row, found in zip(group, results, strict=True):
                stats["candidates"] += 1
                for it in found:
                    db.conn.execute(
                        "INSERT OR IGNORE INTO want_item(post_id, request_type, subject, "
                        "want_summary, referenced_games, referenced_appids, intensity, quote, "
                        "confidence, extractor_version) VALUES (?,?,?,?,?,?,?,?,?,?)",
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
            db.conn.commit()
            if stats["candidates"] // 10 != before // 10 or len(group) > 1:
                print(
                    f"  {stats['candidates']}/{len(work)} posts, {stats['items']} items",
                    file=sys.stderr,
                )
    finally:
        db.conn.commit()
    return stats
