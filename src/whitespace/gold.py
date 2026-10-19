"""Gold-set tooling: sample extractions to label by hand, then measure precision.

Two samples:
  items   extracted wants -> label each `correct` y/n          => precision of what we extract
  missed  trigger posts that produced nothing -> `should_extract` y/n  => how often we miss

Nothing here uses a model. You label, the script counts.
"""

from __future__ import annotations

import csv
import random
from pathlib import Path

from whitespace.extract.prefilter import is_any_candidate
from whitespace.storage import Database

ITEM_FIELDS = [
    "want_id",
    "correct",
    "quote",
    "want_summary",
    "request_type",
    "subject",
    "scope",
    "intensity",
    "confidence",
    "context_game",
    "extractor_version",
    "post_excerpt",
]
MISSED_FIELDS = ["post_id", "should_extract", "source", "text_excerpt"]

_YES = {"y", "yes", "1", "true", "t"}
_NO = {"n", "no", "0", "false", "f"}


def parse_label(value: str | None) -> bool | None:
    v = (value or "").strip().lower()
    if v in _YES:
        return True
    if v in _NO:
        return False
    return None


def sample_items(db: Database, n: int, seed: int = 42) -> list[dict]:
    rows = db.conn.execute(
        "SELECT w.id, w.quote, w.want_summary, w.request_type, w.subject, w.scope, w.intensity, "
        "w.confidence, w.extractor_version AS ver, c.name AS game, "
        "substr(p.text, 1, 300) AS excerpt "
        "FROM want_item w JOIN post p ON p.id = w.post_id "
        "LEFT JOIN catalog_game c ON c.appid = w.context_appid ORDER BY w.id"
    ).fetchall()
    chosen = random.Random(seed).sample(rows, min(n, len(rows)))
    return [
        {
            "want_id": r["id"],
            "correct": "",
            "quote": r["quote"],
            "want_summary": r["want_summary"],
            "request_type": r["request_type"],
            "subject": r["subject"],
            "scope": r["scope"],
            "intensity": r["intensity"],
            "confidence": r["confidence"],
            "context_game": r["game"] or "",
            "extractor_version": r["ver"] or "",
            "post_excerpt": (r["excerpt"] or "").replace("\n", " "),
        }
        for r in chosen
    ]


def sample_missed(db: Database, n: int, seed: int = 42) -> list[dict]:
    """Cleaned, non-duplicate posts that match a trigger phrase but have no extracted want."""
    rows = db.conn.execute(
        "SELECT p.id, p.source, p.text FROM post p LEFT JOIN want_item w ON w.post_id = p.id "
        "WHERE w.id IS NULL AND json_extract(p.flags,'$.is_dup') = 0 "
        "AND json_extract(p.flags,'$.is_low_effort') = 0 ORDER BY p.id"
    ).fetchall()
    candidates = [r for r in rows if is_any_candidate(r["text"] or "")]
    chosen = random.Random(seed).sample(candidates, min(n, len(candidates)))
    return [
        {
            "post_id": r["id"],
            "should_extract": "",
            "source": r["source"],
            "text_excerpt": (r["text"] or "")[:400].replace("\n", " "),
        }
        for r in chosen
    ]


def write_csv(path: str | Path, fields: list[str], rows: list[dict]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:  # BOM so Excel reads UTF-8
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def read_csv(path: str | Path) -> list[dict]:
    # errors="replace": Excel's plain "CSV" export is not UTF-8; accents may degrade, rows survive
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as f:
        return list(csv.DictReader(f))


def _precision(rows: list[dict], key: str) -> dict:
    labels = [parse_label(r.get("correct")) for r in rows]
    yes, no = labels.count(True), labels.count(False)
    return {"n": yes + no, "precision": round(yes / (yes + no), 3) if yes + no else None}


def _group(rows: list[dict], key: str) -> dict[str, dict]:
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(str(r.get(key) or "(none)"), []).append(r)
    return {k: _precision(v, key) for k, v in sorted(groups.items())}


def score_items(rows: list[dict], conf_threshold: float = 0.6) -> dict:
    """Precision overall, by field, and above/below a confidence threshold (does it help?)."""
    labeled = [r for r in rows if parse_label(r.get("correct")) is not None]

    def conf(r: dict) -> float:
        try:
            return float(r.get("confidence") or 0)
        except ValueError:
            return 0.0

    hi = [r for r in labeled if conf(r) >= conf_threshold]
    lo = [r for r in labeled if conf(r) < conf_threshold]
    return {
        "labeled": len(labeled),
        "unlabeled": len(rows) - len(labeled),
        "overall": _precision(labeled, "correct"),
        "by_request_type": _group(labeled, "request_type"),
        "by_subject": _group(labeled, "subject"),
        "by_scope": _group(labeled, "scope"),
        "by_extractor": _group(labeled, "extractor_version"),
        f"confidence>={conf_threshold}": _precision(hi, "correct"),
        f"confidence<{conf_threshold}": _precision(lo, "correct"),
    }


def score_missed(rows: list[dict]) -> dict:
    """Among trigger posts we extracted nothing from, how many should have produced a want."""
    labels = [parse_label(r.get("should_extract")) for r in rows]
    yes, no = labels.count(True), labels.count(False)
    return {
        "labeled": yes + no,
        "unlabeled": len(rows) - yes - no,
        "missed_rate": round(yes / (yes + no), 3) if yes + no else None,
    }


UNTRIGGERED_FIELDS = ["post_id", "contains_wish", "source", "text_excerpt"]


def sample_untriggered(db: Database, n: int, seed: int = 42) -> list[dict]:
    """Cleaned posts that match NO trigger phrase. If a real share of these contain a wish, the
    prefilter is the bottleneck (and the labeled ones show which patterns to add)."""
    rows = db.conn.execute(
        "SELECT p.id, p.source, p.text FROM post p LEFT JOIN want_item w ON w.post_id = p.id "
        "WHERE w.id IS NULL AND json_extract(p.flags,'$.is_dup') = 0 "
        "AND json_extract(p.flags,'$.is_low_effort') = 0 ORDER BY p.id"
    ).fetchall()
    others = [r for r in rows if not is_any_candidate(r["text"] or "")]
    chosen = random.Random(seed).sample(others, min(n, len(others)))
    return [
        {
            "post_id": r["id"],
            "contains_wish": "",
            "source": r["source"],
            "text_excerpt": (r["text"] or "")[:400].replace("\n", " "),
        }
        for r in chosen
    ]


def score_untriggered(rows: list[dict]) -> dict:
    labels = [parse_label(r.get("contains_wish")) for r in rows]
    yes, no = labels.count(True), labels.count(False)
    return {
        "labeled": yes + no,
        "unlabeled": len(rows) - yes - no,
        "wish_rate": round(yes / (yes + no), 3) if yes + no else None,
    }


# kind -> (default file name, CSV fields, the column you fill in with y / n)
KINDS = {
    "items": ("gold_items.csv", ITEM_FIELDS, "correct"),
    "missed": ("gold_missed.csv", MISSED_FIELDS, "should_extract"),
    "untriggered": ("gold_untriggered.csv", UNTRIGGERED_FIELDS, "contains_wish"),
}
