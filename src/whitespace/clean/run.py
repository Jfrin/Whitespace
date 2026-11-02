from __future__ import annotations

import json

from whitespace.clean.filters import detect_language, is_low_effort, shingles
from whitespace.storage import Database

CHUNK_SIZE = 128


def _index_post(db: Database, post_id: int, words: set[str]) -> None:
    db.conn.execute(
        "INSERT INTO clean_fingerprint(post_id, shingle_count) VALUES (?, ?)",
        (post_id, len(words)),
    )
    db.conn.executemany(
        "INSERT INTO clean_shingle(shingle, post_id) VALUES (?, ?)",
        ((word, post_id) for word in words),
    )


def _index_existing(db: Database) -> None:
    """Index pre-existing cleaned posts once, including databases from older releases."""
    if db.conn.execute("SELECT 1 FROM clean_index_state WHERE id=1 AND ready=1").fetchone():
        return
    last_id = 0
    while True:
        rows = db.conn.execute(
            "SELECT p.id, p.text FROM post p WHERE p.id > ? AND p.flags != '{}' "
            "AND NOT EXISTS (SELECT 1 FROM clean_fingerprint f WHERE f.post_id=p.id) "
            "ORDER BY p.id LIMIT ?",
            (last_id, CHUNK_SIZE),
        ).fetchall()
        if not rows:
            with db.conn:
                db.conn.execute("INSERT OR REPLACE INTO clean_index_state(id, ready) VALUES (1, 1)")
            return
        with db.conn:
            for row in rows:
                _index_post(db, row["id"], shingles(row["text"] or ""))
        last_id = rows[-1]["id"]


def _near_duplicate(db: Database, words: set[str]) -> bool:
    # Count exact shared shingles through the inverted index. Jaccard >= 0.8
    # is equivalent to 9 * intersection >= 4 * (size_a + size_b).
    db.conn.execute("DELETE FROM temp.clean_query_shingle")
    db.conn.executemany(
        "INSERT INTO temp.clean_query_shingle(shingle) VALUES (?)",
        ((word,) for word in words),
    )
    candidates = db.conn.execute(
        "SELECT s.post_id FROM temp.clean_query_shingle q "
        "CROSS JOIN clean_shingle s ON s.shingle=q.shingle "
        "JOIN clean_fingerprint f ON f.post_id=s.post_id "
        "GROUP BY s.post_id HAVING 9*COUNT(*) >= 4*(? + f.shingle_count) LIMIT 1",
        (len(words),),
    )
    return candidates.fetchone() is not None


def clean_posts(db: Database, refresh: bool = False, min_words: int = 8) -> dict[str, int]:
    """Annotate posts in bounded transactions; flags and duplicate index commit together."""
    if refresh:
        with db.conn:
            db.conn.execute("UPDATE post SET flags='{}'")
            db.conn.execute("DELETE FROM clean_shingle")
            db.conn.execute("DELETE FROM clean_fingerprint")
            db.conn.execute("DELETE FROM clean_index_state")
    db.conn.execute(
        "CREATE TEMP TABLE IF NOT EXISTS clean_query_shingle "
        "(shingle TEXT PRIMARY KEY) WITHOUT ROWID"
    )
    _index_existing(db)
    counts = {"processed": 0, "dup": 0, "low_effort": 0}
    while True:
        rows = db.conn.execute(
            "SELECT id, text, lang, content_hash FROM post WHERE flags='{}' "
            "ORDER BY created_at, id LIMIT ?",
            (CHUNK_SIZE,),
        ).fetchall()
        if not rows:
            break
        with db.conn:
            for row in rows:
                text = row["text"] or ""
                words = shingles(text)
                exact = (
                    db.conn.execute(
                        "SELECT 1 FROM post WHERE content_hash=? AND flags != '{}' LIMIT 1",
                        (row["content_hash"],),
                    ).fetchone()
                    is not None
                )
                flags = {
                    "is_dup": exact or _near_duplicate(db, words),
                    "is_low_effort": is_low_effort(text, min_words),
                }
                _index_post(db, row["id"], words)
                db.conn.execute(
                    "UPDATE post SET flags=?, lang=? WHERE id=?",
                    (json.dumps(flags), row["lang"] or detect_language(text), row["id"]),
                )
                counts["processed"] += 1
                counts["dup"] += int(flags["is_dup"])
                counts["low_effort"] += int(flags["is_low_effort"])
    return counts
