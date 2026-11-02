from whitespace import gold
from whitespace.models import Post


def test_parse_label():
    assert gold.parse_label("Y") is True and gold.parse_label(" no ") is False
    assert gold.parse_label("1") is True and gold.parse_label("0") is False
    assert gold.parse_label("") is None and gold.parse_label("maybe") is None
    assert gold.parse_label(None) is None


def test_sample_items_is_seeded_and_capped(db, add_want):
    for i in range(10):
        add_want(str(i), summary=f"want {i}")
    a = gold.sample_items(db, 4, seed=1)
    b = gold.sample_items(db, 4, seed=1)
    assert [r["want_id"] for r in a] == [r["want_id"] for r in b] and len(a) == 4
    assert len(gold.sample_items(db, 99)) == 10
    assert set(a[0]) == set(gold.ITEM_FIELDS) and a[0]["correct"] == ""


def test_csv_roundtrip_keeps_unicode_quotes_and_newlines(tmp_path):
    text = 'héllo, "quoted"\nnext line'
    path = tmp_path / "out" / "g.csv"
    row_in = {"post_id": 1, "should_extract": "y", "source": "s", "text_excerpt": text}
    gold.write_csv(path, gold.MISSED_FIELDS, [row_in])
    [row] = gold.read_csv(path)
    assert row["text_excerpt"] == text and row["should_extract"] == "y" and row["post_id"] == "1"


def test_score_items_precision_breakdowns_and_confidence_split():
    rows = [
        {
            "correct": "y",
            "request_type": "feature_wish",
            "subject": "mechanic",
            "scope": "game_specific",
            "confidence": "0.9",
        },
        {
            "correct": "y",
            "request_type": "feature_wish",
            "subject": "mechanic",
            "scope": "game_specific",
            "confidence": "0.8",
        },
        {
            "correct": "n",
            "request_type": "games_like",
            "subject": "other",
            "scope": "reference",
            "confidence": "0.4",
        },
        {
            "correct": "",
            "request_type": "feature_wish",
            "subject": "other",
            "scope": "x",
            "confidence": "0.9",
        },  # unlabeled
    ]
    out = gold.score_items(rows)
    assert out["labeled"] == 3 and out["unlabeled"] == 1
    assert out["overall"] == {"n": 3, "precision": 0.667}
    assert out["by_request_type"]["games_like"] == {"n": 1, "precision": 0.0}
    assert out["by_subject"]["mechanic"]["precision"] == 1.0
    assert out["confidence>=0.6"] == {"n": 2, "precision": 1.0}
    assert out["confidence<0.6"] == {"n": 1, "precision": 0.0}


def test_score_with_no_labels_does_not_divide_by_zero():
    out = gold.score_items([{"correct": "", "confidence": "0.5"}])
    assert out["overall"] == {"n": 0, "precision": None}
    assert gold.score_missed([{"should_extract": ""}])["missed_rate"] is None


def test_score_missed():
    rows = [{"should_extract": "y"}, {"should_extract": "n"}, {"should_extract": ""}]
    assert gold.score_missed(rows) == {"labeled": 2, "unlabeled": 1, "missed_rate": 0.5}


def test_sample_missed_only_cleaned_trigger_posts_without_wants(db, add_want):
    add_want("w")  # has a want
    db.upsert_post(Post("s", "c", "u", "I wish there was a farming game with automation", 1))
    db.upsert_post(Post("s", "d", "u", "Great game, played for 200 hours and loved it a lot", 2))
    db.upsert_post(Post("s", "e", "u", "I wish there was a farming game with automation too", 3))
    for r in db.conn.execute("SELECT id, source_id FROM post").fetchall():
        db.set_flags(r["id"], "en", {"is_dup": r["source_id"] == "e", "is_low_effort": False})
    rows = gold.sample_missed(db, 10)
    assert len(rows) == 1 and "farming game" in rows[0]["text_excerpt"]
    assert rows[0]["should_extract"] == "" and set(rows[0]) == set(gold.MISSED_FIELDS)


def test_sample_untriggered_is_the_complement_of_missed(db):
    db.upsert_post(Post("s", "t", "u", "I wish there was a farming game with automation", 1))
    db.upsert_post(Post("s", "n1", "u", "Needs more content, the ending felt rushed and short", 2))
    db.upsert_post(Post("s", "n2", "u", "Great game, played for 200 hours and loved it a lot", 3))
    for r in db.conn.execute("SELECT id FROM post").fetchall():
        db.set_flags(r["id"], "en", {"is_dup": False, "is_low_effort": False})
    rows = gold.sample_untriggered(db, 10)
    texts = " ".join(r["text_excerpt"] for r in rows)
    assert len(rows) == 2 and "farming game" not in texts and "Needs more content" in texts
    assert set(rows[0]) == set(gold.UNTRIGGERED_FIELDS)
    assert len(gold.sample_missed(db, 10)) == 1  # the trigger post, which has no want


def test_score_untriggered_and_kind_table():
    rows = [{"contains_wish": v} for v in ("y", "n", "n", "")]
    assert gold.score_untriggered(rows) == {"labeled": 3, "unlabeled": 1, "wish_rate": 0.333}
    assert set(gold.KINDS) == {"items", "missed", "untriggered", "rules"}


def test_rules_sample_is_english_and_independent_of_existing_wants(db, add_want):
    add_want("already-extracted")
    db.upsert_post(Post("s", "english", "u", "I wish it had a map.\nMore detail.", 1))
    db.upsert_post(Post("s", "russian", "u", "review", 2))
    db.upsert_post(Post("s", "unknown", "u", "unknown", 3))
    db.upsert_post(Post("s", "duplicate", "u", "duplicate", 4))
    for row in db.conn.execute("SELECT id, source_id FROM post").fetchall():
        lang = {"russian": "russian", "unknown": None}.get(row["source_id"], "en")
        db.set_flags(
            row["id"], lang, {"is_dup": row["source_id"] == "duplicate", "is_low_effort": False}
        )
    rows = gold.sample_rules_posts(db, 10, seed=7)
    assert len(rows) == 2 and all(r["contains_want"] == "" for r in rows)
    assert {r["text"] for r in rows} == {
        "text already-extracted",
        "I wish it had a map.\nMore detail.",
    }
    assert gold.sample_rules_posts(db, 1, seed=7) == gold.sample_rules_posts(db, 1, seed=7)


def test_rules_post_score_includes_prefilter_and_ignores_unlabeled():
    rows = [
        {"post_id": "tp", "contains_want": "y", "text": "I wish for a map"},
        {"post_id": "fn", "contains_want": "y", "text": "More maps please"},
        {"post_id": "fp", "contains_want": "n", "text": "I wish for a refund"},
        {"post_id": "tn", "contains_want": "n", "text": "Great game"},
        {"post_id": "blank", "contains_want": "", "text": "I wish for a map"},
    ]
    seen = []

    def extractor(text):
        seen.append(text)
        return [object()]

    result = gold.score_rules_posts(rows, extractor)
    assert result["labeled"] == 4 and result["unlabeled"] == 1
    assert (result["true_positive"], result["false_positive"]) == (1, 1)
    assert (result["true_negative"], result["false_negative"]) == (1, 1)
    assert result["precision"] == 0.5 and result["recall"] == 0.5
    assert {error["post_id"] for error in result["errors"]} == {"fn", "fp"}
    assert len(seen) == 2  # unlabeled and untriggered posts never reach the extractor


def test_rules_score_no_labels_has_no_metrics():
    result = gold.score_rules_posts([{"contains_want": "", "text": "I wish"}], lambda _: [])
    assert result["precision"] is None and result["recall"] is None


def test_read_csv_survives_a_non_utf8_file(tmp_path):
    path = tmp_path / "excel.csv"
    path.write_bytes("post_id,should_extract\n1,y\n2,caf\xe9\n".encode("latin-1"))
    rows = gold.read_csv(path)
    assert [r["post_id"] for r in rows] == ["1", "2"]
