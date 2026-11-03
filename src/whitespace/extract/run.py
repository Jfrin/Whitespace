from __future__ import annotations

import json
import sys
from collections.abc import Callable, Iterator
from itertools import islice

from whitespace.extract.errors import ExtractorStop
from whitespace.extract.nlp import EXTRACTOR_VERSION
from whitespace.extract.prefilter import is_any_candidate
from whitespace.extract.rules import is_english
from whitespace.extract.schema import WantItem
from whitespace.storage import Database

Extractor = Callable[[str], list[WantItem]]

ELIGIBLE = (
    "NOT EXISTS (SELECT 1 FROM want_item w WHERE w.post_id=p.id) "
    "AND NOT EXISTS (SELECT 1 FROM extraction_attempt a WHERE a.post_id=p.id "
    "AND a.extractor_version=?) AND json_extract(p.flags,'$.is_dup')=0 "
    "AND json_extract(p.flags,'$.is_low_effort')=0"
)


def _next_candidate(db, appid, after, version, prefilter, english_only, stats, eligible):
    rows = db.conn.execute(
        "SELECT p.id, p.text, p.lang FROM post p WHERE p.game_appid IS ? AND p.id > ? AND "
        + eligible
        + " ORDER BY p.id",
        (appid, after, version, version) if eligible != ELIGIBLE else (appid, after, version),
    )
    for row in rows:
        stats["scanned"] += 1
        if english_only and not is_english(row["lang"]):
            stats["non_english"] += 1
        elif prefilter == "all" or is_any_candidate(row["text"] or ""):
            return row["id"]
    return None


def _candidates(
    db: Database,
    version: str,
    prefilter: str,
    english_only: bool,
    stats: dict[str, int],
    compare_versions: bool,
) -> Iterator:
    """Round-robin per game, keeping queue state in SQLite instead of loading the backlog."""
    db.conn.execute("DROP TABLE IF EXISTS temp.extract_game_queue")
    db.conn.execute(
        "CREATE TEMP TABLE extract_game_queue (slot INTEGER PRIMARY KEY, game_appid INTEGER, "
        "first_id INTEGER NOT NULL, next_id INTEGER NOT NULL, turn INTEGER NOT NULL)"
    )
    db.conn.execute("CREATE INDEX temp.extract_game_turn ON extract_game_queue(turn, first_id)")
    eligible = ELIGIBLE
    if compare_versions:
        eligible = ELIGIBLE.replace("w.post_id=p.id)", "w.post_id=p.id AND w.extractor_version=?)")
    try:
        games = db.conn.execute(
            "SELECT p.game_appid FROM post p WHERE " + eligible + " GROUP BY p.game_appid",
            (version, version) if compare_versions else (version,),
        )
        for game in games:
            appid = game["game_appid"]
            first = _next_candidate(db, appid, 0, version, prefilter, english_only, stats, eligible)
            if first is not None:
                db.conn.execute(
                    "INSERT INTO temp.extract_game_queue(game_appid, first_id, next_id, turn) "
                    "VALUES (?, ?, ?, 0)",
                    (appid, first, first),
                )
        while True:
            state = db.conn.execute(
                "SELECT * FROM temp.extract_game_queue ORDER BY turn, first_id LIMIT 1"
            ).fetchone()
            if state is None:
                return
            row = db.conn.execute("SELECT id, text FROM post WHERE id=?", (state["next_id"],))
            yield row.fetchone()
            nxt = _next_candidate(
                db,
                state["game_appid"],
                state["next_id"],
                version,
                prefilter,
                english_only,
                stats,
                eligible,
            )
            if nxt is None:
                db.conn.execute(
                    "DELETE FROM temp.extract_game_queue WHERE slot=?", (state["slot"],)
                )
            else:
                db.conn.execute(
                    "UPDATE temp.extract_game_queue SET next_id=?, turn=turn+1 WHERE slot=?",
                    (nxt, state["slot"]),
                )
    finally:
        db.conn.execute("DROP TABLE IF EXISTS temp.extract_game_queue")


def extract_all(
    db: Database,
    extractor: Extractor,
    limit: int | None = None,
    redo: bool = False,
    prefilter: str = "cue",
    english_only: bool = True,
    compare_versions: bool = False,
) -> dict[str, int]:
    """Extract in bounded batches; completed posts and responses remain resumable."""
    if redo and compare_versions:
        raise ValueError("redo cannot be combined with version comparison")
    if compare_versions:
        db.enable_versioned_wants()
    if redo:
        db.conn.execute("DELETE FROM want_item")
        db.conn.execute("DELETE FROM extraction_attempt")
        db.conn.commit()
    version = getattr(extractor, "version", EXTRACTOR_VERSION)
    stats = {"scanned": 0, "candidates": 0, "items": 0, "non_english": 0}
    batcher = getattr(extractor, "extract_batch", None)
    size = int(getattr(extractor, "batch_size", 1) or 1) if batcher else 1
    work = _candidates(db, version, prefilter, english_only, stats, compare_versions)
    print("Selecting posts by game. Completed batches are saved.", file=sys.stderr)
    try:
        while limit is None or limit <= 0 or stats["candidates"] < limit:
            remaining = size if not limit or limit < 0 else min(size, limit - stats["candidates"])
            group = list(islice(work, remaining))
            if not group:
                break
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
            with db.conn:
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
                    outcome_check = getattr(extractor, "cache_outcome", None)
                    cache_check = getattr(extractor, "is_cached", None)
                    outcome = (
                        outcome_check(row["text"] or "")
                        if outcome_check
                        else "completed"
                        if not cache_check or cache_check(row["text"] or "")
                        else None
                    )
                    if outcome:
                        db.conn.execute(
                            "INSERT OR IGNORE INTO extraction_attempt"
                            "(post_id, extractor_version, outcome) "
                            "VALUES (?, ?, ?)",
                            (row["id"], version, outcome),
                        )
            if stats["candidates"] // 10 != before // 10 or len(group) > 1:
                print(f"  {stats['candidates']} posts, {stats['items']} items", file=sys.stderr)
    finally:
        work.close()
        # Queue maintenance can open a transaction after the last completed batch.
        # Earlier behavior also committed the connection when extraction ended.
        if db.conn.in_transaction:
            db.conn.commit()
    return stats
