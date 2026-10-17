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
