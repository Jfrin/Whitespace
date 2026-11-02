import json

import pytest

from whitespace.extract.appid import AppIndex
from whitespace.extract.gazetteer import load_index
from whitespace.extract.prefilter import (
    find_trigger,
    has_lack_cue,
    is_any_candidate,
    is_candidate,
    match_patterns,
)
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
    assert stats == {"scanned": 2, "candidates": 1, "items": 1, "non_english": 0} and len(seen) == 1
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


def test_shipped_games_config_loads_with_aliases_for_partial_names():
    idx = load_index("config/games.yaml")
    assert idx.resolve("stardew") == 413150
    assert idx.resolve("DBD") == 381210 and idx.resolve("gungeon") == 311690
    assert idx.resolve("FTL") == 212680 and idx.resolve("Binding") == 250900


def test_extract_all_redo_replaces_existing_wants(db):
    db.upsert_post(Post("s", "1", "u", "I wish there was a co-op farming game with automation", 1))
    _flag_all(db)

    def old_rules(text):
        return [WantItem("feature_wish", "mechanic", "old summary", 2, "q", 0.8)]

    def new_rules(text):
        return [WantItem("feature_wish", "mechanic", "new summary", 2, "q", 0.8)]

    extract_all(db, old_rules)
    assert extract_all(db, new_rules)["candidates"] == 0  # already extracted: skipped
    extract_all(db, new_rules, redo=True)
    rows = [r["want_summary"] for r in db.conn.execute("SELECT want_summary FROM want_item")]
    assert rows == ["new summary"]


def test_version_comparison_preserves_existing_wants_and_equal_summaries(db):
    db.upsert_post(Post("s", "1", "u", "I wish there was a co-op farming game", 1))
    _flag_all(db)

    class Versioned:
        def __init__(self, version):
            self.version = version

        def __call__(self, text):
            return [WantItem("feature_wish", "mechanic", "same summary", 2, "q", 0.8)]

    assert extract_all(db, Versioned("v1"))["items"] == 1
    original = db.conn.execute("SELECT id FROM want_item").fetchone()[0]
    db.conn.execute("INSERT INTO cluster_member(want_item_id) VALUES (?)", (original,))
    db.conn.commit()
    assert extract_all(db, Versioned("v2"), compare_versions=True)["items"] == 1
    assert extract_all(db, Versioned("v2"), compare_versions=True)["candidates"] == 0
    assert [
        row[0] for row in db.conn.execute("SELECT extractor_version FROM want_item ORDER BY id")
    ] == ["v1", "v2"]
    assert db.conn.execute("SELECT want_item_id FROM cluster_member").fetchone()[0] == original
    with pytest.raises(ValueError, match="redo"):
        extract_all(db, Versioned("v3"), redo=True, compare_versions=True)


def test_version_migration_keeps_custom_columns_and_indexes(db):
    db.conn.execute("ALTER TABLE want_item ADD COLUMN custom_note TEXT")
    db.conn.execute("CREATE INDEX user_want_note ON want_item(custom_note)")
    db.conn.commit()
    db.enable_versioned_wants()
    assert "custom_note" in {row["name"] for row in db.conn.execute("PRAGMA table_info(want_item)")}
    assert db.conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name='user_want_note'"
    ).fetchone()


def test_version_migration_does_not_commit_unrelated_pending_work(db):
    db.conn.execute("INSERT INTO post(source, source_id) VALUES ('s', 'pending')")
    with pytest.raises(ValueError, match="pending changes"):
        db.enable_versioned_wants()
    db.conn.rollback()
    assert db.count("post") == 0


def test_extract_all_skips_non_english_posts(db):
    text = "I wish there was a co-op farming game with automation"
    db.upsert_post(Post("s", "1", "u", text, 1))
    db.upsert_post(Post("s", "2", "u", text + " too", 2))
    _flag_all(db)
    db.conn.execute("UPDATE post SET lang='russian' WHERE source_id='2'")
    db.conn.commit()
    seen = []
    stats = extract_all(db, lambda t: (seen.append(t), [])[1])
    assert stats["non_english"] == 1 and stats["candidates"] == 1 and len(seen) == 1


def test_limited_runs_share_posts_across_games_and_advance_after_empty_results(db):
    for appid in (1, 2):
        for i in range(3):
            db.upsert_post(Post("s", f"{appid}-{i}", "u", f"text {appid}-{i}", i, game_appid=appid))
    _flag_all(db)
    seen = []

    def no_wants(text):
        seen.append(text)
        return []

    assert extract_all(db, no_wants, limit=4, prefilter="all")["candidates"] == 4
    assert seen == ["text 1-0", "text 2-0", "text 1-1", "text 2-1"]
    assert extract_all(db, no_wants, limit=4, prefilter="all")["candidates"] == 2
    assert seen[-2:] == ["text 1-2", "text 2-2"]
    assert extract_all(db, no_wants, limit=4, prefilter="all")["candidates"] == 0
    assert db.count("extraction_attempt") == 6 and db.count("post") == 6


def test_fairness_uses_first_candidate_not_first_ineligible_post(db):
    entries = [
        (1, "ordinary review"),
        (2, "I wish for a map"),
        (None, "I wish for a compass"),
        (1, "I wish for co-op"),
        (2, "I wish for a tutorial"),
    ]
    for i, (appid, text) in enumerate(entries):
        db.upsert_post(Post("s", str(i), "u", text, i, game_appid=appid))
    _flag_all(db)
    seen = []
    extract_all(db, lambda text: (seen.append(text), [])[1], limit=4)
    assert seen == [
        "I wish for a map",
        "I wish for a compass",
        "I wish for co-op",
        "I wish for a tutorial",
    ]


def test_limited_extraction_does_not_materialize_entire_large_backlog(db):
    flags = json.dumps({"is_dup": False, "is_low_effort": False})
    db.conn.executemany(
        "INSERT INTO post(source, source_id, text, game_appid, lang, flags) VALUES (?,?,?,?,?,?)",
        (("s", str(i), f"review {i}", i % 4, "en", flags) for i in range(4000)),
    )
    db.conn.commit()
    stats = extract_all(db, lambda text: [], limit=7, prefilter="all")
    assert stats["candidates"] == 7 and stats["scanned"] <= 11
    plan = " ".join(
        row[3]
        for row in db.conn.execute(
            "EXPLAIN QUERY PLAN SELECT id FROM post WHERE game_appid IS ? AND id > ? ORDER BY id",
            (1, 0),
        )
    )
    assert "post_game_id" in plan


def test_invalid_batch_rolls_back_partial_results(db):
    for i in range(2):
        db.upsert_post(Post("s", str(i), "u", f"review {i}", i, game_appid=1))
    _flag_all(db)

    class BadBatch:
        batch_size = 2

        def __call__(self, text):
            return []

        def extract_batch(self, texts):
            return [[WantItem("feature_wish", "ux", "map", 2, "q", 0.8)]]

    with pytest.raises(ValueError):
        extract_all(db, BadBatch(), prefilter="all")
    assert db.count("want_item") == db.count("extraction_attempt") == 0
    assert extract_all(db, lambda text: [], prefilter="all")["candidates"] == 2


def test_prefilter_handles_curly_apostrophes():
    assert is_candidate("Why doesn\u2019t anyone make a cozy sim?")
    assert find_trigger("it\u2019s fine. why doesn\u2019t anyone make a sim") == 11


def test_games_from_the_first_gold_review_resolve():
    idx = load_index("config/games.yaml")
    assert idx.resolve("dead cells") == 588650 and idx.resolve("CS2") == 730
    assert idx.resolve("Wizard of Legend") == 445980


@pytest.mark.parametrize(
    "text",
    [
        "There is no way to rebind controls",
        "You can't skip the dialogue",
        "cant skip it",
        "It doesn\u2019t have a map",
        "It lacks checkpoints",
        "Not enough content",
        "unplayable without the wiki",
        "very hard to know what to do",
        "missing a tutorial",
    ],
)
def test_lack_cues_hit(text):
    assert has_lack_cue(text) and is_any_candidate(text)


def test_lack_cues_miss_plain_praise():
    assert not has_lack_cue("Great game, played for 200 hours and loved it")
    assert not is_any_candidate("I love the music and the art")
    assert is_any_candidate("I wish it had co-op")  # wish triggers still count
