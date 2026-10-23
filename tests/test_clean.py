import json

from whitespace.clean.filters import (
    detect_language,
    detect_review_bombs,
    is_low_effort,
    is_near_duplicate,
    shingles,
)
from whitespace.clean.run import clean_posts
from whitespace.models import Post


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
    db.upsert_post(Post("s", "1", "u", "needs more checkpoints please add", 1))   # 5 words
    clean_posts(db)

    def low():
        return json.loads(db.conn.execute("SELECT flags FROM post").fetchone()["flags"])[
            "is_low_effort"
        ]

    assert low() is True
    assert clean_posts(db)["processed"] == 0                  # nothing new without refresh
    assert clean_posts(db, refresh=True, min_words=4)["processed"] == 1
    assert low() is False
