from __future__ import annotations

from gamegap.clean.filters import (
    detect_language,
    is_low_effort,
    is_near_duplicate,
    shingles,
)
from gamegap.storage import Database


def clean_posts(db: Database) -> dict[str, int]:
    """Annotate unflagged posts with lang + flags. Flags are reversible, nothing is deleted."""
    seen_hashes: set[str] = {
        r["content_hash"]
        for r in db.conn.execute("SELECT content_hash FROM post WHERE flags != '{}'")
    }
    seen_shingles: list[set[str]] = []
    counts = {"processed": 0, "dup": 0, "low_effort": 0}
    for row in db.posts_without_flags():
        text = row["text"] or ""
        flags = {
            "is_dup": row["content_hash"] in seen_hashes or is_near_duplicate(text, seen_shingles),
            "is_low_effort": is_low_effort(text),
        }
        seen_hashes.add(row["content_hash"])
        seen_shingles.append(shingles(text))
        db.set_flags(row["id"], row["lang"] or detect_language(text), flags)
        counts["processed"] += 1
        counts["dup"] += int(flags["is_dup"])
        counts["low_effort"] += int(flags["is_low_effort"])
    return counts
