import json

import pytest

from whitespace.extract.appid import AppIndex
from whitespace.extract.gazetteer import load_index
from whitespace.extract.prefilter import find_trigger, is_candidate, match_patterns
from whitespace.extract.run import extract_all
from whitespace.extract.schema import WantItem, validate_item
from whitespace.models import Post

GOOD = {
    "request_type": "feature_wish",
    "subject": "mechanic",
    "want_summary": "co-op farming",
    "referenced_games": ["Stardew Valley"],
    "referenced_appids": [413150],
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
        "I want a farming sim",
        "I'd kill for a good sim",
        "Is there a game with romance?",
    ],
)
def test_prefilter_hits(text):
    assert is_candidate(text) and match_patterns(text)


def test_prefilter_misses_and_trigger_offset():
    assert not is_candidate("Great game, 10/10, played for 200 hours")
    assert find_trigger("honestly i wish it had x") == 9
    assert find_trigger("no request here") is None


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


def test_validation_accepts_good_and_rejects_missing_keys():
    assert validate_item(GOOD).referenced_appids == [413150]
    with pytest.raises(ValueError):
        validate_item({"request_type": "feature_wish"})


def _flag_all(db):
    for r in db.conn.execute("SELECT id FROM post").fetchall():
        db.set_flags(r["id"], "en", {"is_dup": False, "is_low_effort": False})


def test_extract_all_only_runs_candidates_and_stores_appids(db):
    db.upsert_post(Post("s", "1", "u", "I wish there was a co-op farming game with automation", 1))
    db.upsert_post(Post("s", "2", "u", "Great game, played for 200 hours and loved it a lot", 2))
    _flag_all(db)
    seen = []

    def fake(text):
        seen.append(text)
        return [
            WantItem("feature_wish", "mechanic", "co-op farming", 2, "q", 0.8, ["Hades"], [1145360])
        ]

    stats = extract_all(db, fake)
    assert stats == {"scanned": 2, "candidates": 1, "items": 1} and len(seen) == 1
    row = db.conn.execute("SELECT * FROM want_item").fetchone()
    assert json.loads(row["referenced_appids"]) == [1145360]
    assert json.loads(row["referenced_games"]) == ["Hades"]
    assert row["extractor_version"].startswith("rules-")
    assert extract_all(db, fake)["candidates"] == 0  # already extracted


def test_extract_all_skips_flagged_posts(db):
    db.upsert_post(Post("s", "1", "u", "I wish there was a co-op farming game with automation", 1))
    db.set_flags(1, "en", {"is_dup": True, "is_low_effort": False})
    assert extract_all(db, lambda t: [])["scanned"] == 0


def test_appid_overrides_exact_and_fuzzy():
    idx = AppIndex({413150: "Stardew Valley", 1145360: "Hades"}, overrides={"SDV": 413150})
    assert idx.resolve("sdv") == 413150
    assert idx.resolve("stardew valley") == 413150
    assert idx.resolve("Stardew Vally") == 413150  # typo -> fuzzy
    assert idx.resolve("Totally Unknown Game") is None
    assert ("SDV", 413150) in idx.patterns() and ("Hades", 1145360) in idx.patterns()


def test_gazetteer_loader_merges_yaml_and_bulk_json(tmp_path):
    (tmp_path / "g.yaml").write_text("games:\n  - {appid: 1, name: Alpha Quest, aliases: [AQ]}\n")
    (tmp_path / "apps.json").write_text(json.dumps({"2": "Beta"}))
    idx = load_index(tmp_path / "g.yaml", tmp_path / "apps.json")
    assert idx.resolve("aq") == 1 and idx.resolve("beta") == 2
    assert load_index(tmp_path / "missing.yaml").resolve("x") is None  # absent files are fine


def test_shipped_games_config_loads():
    assert load_index("config/games.yaml").resolve("stardew") == 413150
