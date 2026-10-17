import json

import pytest

from whitespace.extract.appid import AppIndex
from whitespace.extract.llm import extract_wants, parse_items
from whitespace.extract.prefilter import is_candidate, match_patterns
from whitespace.extract.run import extract_all
from whitespace.extract.schema import validate_item
from whitespace.models import Post

GOOD = {
    "request_type": "feature_wish",
    "subject": "mechanic",
    "want_summary": "co-op farming",
    "referenced_games": ["Stardew Valley"],
    "intensity": 3,
    "quote": "I wish it had co-op",
    "confidence": 0.9,
}


@pytest.mark.parametrize(
    "text",
    [
        "I wish there was a farming game",
        "Why doesn't anyone make a cozy ship sim",
        "looking for a game with base building",
        "games like Hades?",
        "would be perfect if it had co-op",
    ],
)
def test_prefilter_hits(text):
    assert is_candidate(text) and match_patterns(text)


def test_prefilter_misses():
    assert not is_candidate("Great game, 10/10, played for 200 hours")


@pytest.mark.parametrize(
    "patch",
    [
        {"request_type": "nope"},
        {"subject": "nope"},
        {"intensity": 5},
        {"confidence": 2},
        {"want_summary": ""},
        {"quote": ""},
    ],
)
def test_validation_rejects_off_schema(patch):
    with pytest.raises(ValueError):
        validate_item({**GOOD, **patch})


def test_validation_rejects_missing_keys():
    with pytest.raises(ValueError):
        validate_item({"request_type": "feature_wish"})


def test_parse_items_handles_fences_and_drops_bad_items():
    raw = "```json\n" + json.dumps([GOOD, {"junk": 1}]) + "\n```"
    assert len(parse_items(raw)) == 1


def test_extract_wants_retries_once_then_gives_up():
    calls = []

    def flaky(prompt):
        calls.append(prompt)
        return "not json" if len(calls) == 1 else json.dumps([GOOD])

    assert len(extract_wants("I wish...", flaky)) == 1 and len(calls) == 2
    assert extract_wants("x", lambda p: "still not json") == []


def test_extract_all_only_sends_candidates_and_skips_flagged(db):
    db.upsert_post(Post("s", "1", "u", "I wish there was a co-op farming game with automation", 1))
    db.upsert_post(Post("s", "2", "u", "Great game, played for 200 hours and loved it a lot", 2))
    for r in db.conn.execute("SELECT id FROM post").fetchall():
        db.set_flags(r["id"], "en", {"is_dup": False, "is_low_effort": False})
    sent = []
    stats = extract_all(db, lambda p: (sent.append(p), json.dumps([GOOD]))[1])
    assert stats == {"scanned": 2, "candidates": 1, "items": 1} and len(sent) == 1
    assert extract_all(db, lambda p: "[]")["scanned"] == 1  # post 1 already extracted -> skipped


def test_appid_overrides_exact_and_fuzzy():
    idx = AppIndex({413150: "Stardew Valley", 1145360: "Hades"}, overrides={"SDV": 413150})
    assert idx.resolve("sdv") == 413150
    assert idx.resolve("stardew valley") == 413150
    assert idx.resolve("Stardew Vally") == 413150  # typo -> fuzzy
    assert idx.resolve("Totally Unknown Game") is None
