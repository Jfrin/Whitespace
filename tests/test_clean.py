import json
import random
import sqlite3

import pytest

from whitespace.clean.filters import (
    detect_language,
    detect_review_bombs,
    is_low_effort,
    is_near_duplicate,
    shingles,
)
from whitespace.clean.run import _near_duplicate, clean_posts
from whitespace.models import Post
from whitespace.storage import Database


def test_low_effort():
    assert is_low_effort("this game sucks lol")
    assert is_low_effort("sucks " * 20)
    assert not is_low_effort("I really wish there was a farming game with deeper automation")


def test_near_duplicate():
    a = "i wish there was a cozy farming game with deep automation and couples co-op please"
    b = a + " thanks"
    assert is_near_duplicate(b, [shingles(a)])
    assert not is_near_duplicate("completely different sentence about space trading", [shingles(a)])


def test_language_heuristic_fallback():
    assert detect_language("hello there friend") == "en"
    assert detect_language("12345 !!!") == "und"


def test_review_bomb_flags_only_outlier_days():
    counts = {f"2026-01-{d:02d}": 10 + (d % 3) for d in range(1, 15)}
    counts["2026-01-08"] = 400
    assert detect_review_bombs(counts) == ["2026-01-08"]
    assert detect_review_bombs({"d": 1}) == []  # too little history


def test_clean_posts_flags_dups_and_low_effort(db):
    good = "I wish there was a cozy farming sim with deep automation and couples co-op"
    for i, text in enumerate([good, good, "this game sucks lol"]):
        db.upsert_post(Post("s", str(i), "u", text, 100 + i))
    counts = clean_posts(db)
    assert counts == {"processed": 3, "dup": 1, "low_effort": 1}
    assert clean_posts(db)["processed"] == 0  # idempotent: already flagged


def test_cjk_text_is_judged_by_characters_not_spaces():
    assert is_low_effort("好玩")
    assert is_low_effort("ゲームが面白くない")
    long_ja = "ボスが強すぎて何度もやり直したがチェックポイントがなくて辛い。" * 2
    assert not is_low_effort(long_ja)
    assert not is_low_effort("这个游戏的战斗系统很有意思但是没有存档点真的很难受，希望能加上")


def test_clean_refresh_reflags_with_a_new_threshold(db):
    db.upsert_post(Post("s", "1", "u", "needs more checkpoints please add", 1))  # 5 words
    clean_posts(db)

    def low():
        return json.loads(db.conn.execute("SELECT flags FROM post").fetchone()["flags"])[
            "is_low_effort"
        ]

    assert low() is True
    assert clean_posts(db)["processed"] == 0  # nothing new without refresh
    assert clean_posts(db, refresh=True, min_words=4)["processed"] == 1
    assert low() is False


def test_near_duplicates_are_indexed_across_runs_and_database_restart(tmp_path):
    path = tmp_path / "clean.sqlite"
    db = Database(path)
    original = "i wish there was a cozy farming game with deep automation and couples co-op please"
    db.upsert_post(Post("s", "1", "u", original, 1))
    # Simulate a post cleaned by an older release, without the new fingerprint index.
    db.set_flags(1, "en", {"is_dup": False, "is_low_effort": False})
    db.conn.close()
    db = Database(path)
    db.upsert_post(Post("s", "2", "u", original + " thanks", 2))
    assert clean_posts(db)["dup"] == 1
    db.conn.close()
    db = Database(path)
    db.upsert_post(Post("s", "3", "u", original + " thanks again", 3))
    assert clean_posts(db)["dup"] == 1
    assert db.count("clean_fingerprint") == 3


def test_manual_flag_changes_invalidate_only_affected_fingerprint(db):
    text = "this review has plenty of words and describes farming and automation features"
    db.upsert_post(Post("s", "1", "u", text, 1))
    clean_posts(db)
    db.set_flags(1, "en", {})
    assert db.count("clean_fingerprint") == 0
    assert clean_posts(db)["processed"] == 1
    assert db.count("clean_fingerprint") == 1


def test_indexed_duplicates_match_brute_force_across_chunks(db):
    rng = random.Random(123)
    words = [f"word{i}" for i in range(500)]
    texts = []
    for i in range(150):
        if i and i % 5 == 0:
            text = texts[i - 1] + " additional"
        else:
            text = " ".join(rng.sample(words, 20))
        texts.append(text)
        db.upsert_post(Post("s", str(i), "u", text, i))
    expected = []
    seen = []
    for text in texts:
        expected.append(is_near_duplicate(text, seen))
        seen.append(shingles(text))
    clean_posts(db)
    actual = [
        json.loads(row[0])["is_dup"]
        for row in db.conn.execute("SELECT flags FROM post ORDER BY id")
    ]
    assert actual == expected


def test_indexed_jaccard_threshold_is_exact(db):
    base = "one two three four five six"
    db.upsert_post(Post("s", "1", "u", base, 1))
    clean_posts(db)
    assert _near_duplicate(db, shingles(base + " seven"))  # 4 / 5 = 0.8
    assert not _near_duplicate(db, shingles(base + " seven eight"))  # 4 / 6


def test_failed_clean_batch_rolls_back_flags_and_fingerprints(db, monkeypatch):
    monkeypatch.setattr("whitespace.clean.run.CHUNK_SIZE", 2)
    for i in range(3):
        db.upsert_post(Post("s", str(i), "u", f"a sufficiently long review text number {i}", i))
    db.conn.execute(
        "CREATE TRIGGER fail_clean BEFORE UPDATE OF flags ON post WHEN NEW.id=2 "
        "BEGIN SELECT RAISE(ABORT, 'test failure'); END"
    )
    with pytest.raises(sqlite3.IntegrityError, match="test failure"):
        clean_posts(db)
    assert db.count("clean_fingerprint") == 0
    assert db.conn.execute("SELECT count(*) FROM post WHERE flags='{}'").fetchone()[0] == 3
    db.conn.execute("DROP TRIGGER fail_clean")
    assert clean_posts(db)["processed"] == 3


def test_clean_batches_transactions_and_uses_indexes(db, monkeypatch):
    monkeypatch.setattr("whitespace.clean.run.CHUNK_SIZE", 10)
    for i in range(23):
        db.upsert_post(Post("s", str(i), "u", f"review number {i} has enough words for a test", i))
    statements = []
    db.conn.set_trace_callback(statements.append)
    assert clean_posts(db)["processed"] == 23
    db.conn.set_trace_callback(None)
    assert sum(statement == "COMMIT" for statement in statements) == 4  # marker plus three batches
    statements.clear()
    db.conn.set_trace_callback(statements.append)
    assert clean_posts(db)["processed"] == 0
    db.conn.set_trace_callback(None)
    assert not any("NOT EXISTS (SELECT 1 FROM clean_fingerprint" in sql for sql in statements)
    plan = " ".join(
        row[3]
        for row in db.conn.execute(
            "EXPLAIN QUERY PLAN SELECT id FROM post WHERE flags='{}' "
            "ORDER BY created_at, id LIMIT 10"
        )
    )
    assert "post_flags_created" in plan
    hash_plan = " ".join(
        row[3]
        for row in db.conn.execute(
            "EXPLAIN QUERY PLAN SELECT id FROM post WHERE content_hash=?", ("hash",)
        )
    )
    assert "post_content_hash" in hash_plan
    shingle_plan = " ".join(
        row[3]
        for row in db.conn.execute(
            "EXPLAIN QUERY PLAN SELECT s.post_id FROM temp.clean_query_shingle q "
            "CROSS JOIN clean_shingle s ON s.shingle=q.shingle"
        )
    )
    assert "PRIMARY KEY (shingle=?)" in shingle_plan
