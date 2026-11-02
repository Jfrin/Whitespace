import pytest

from whitespace.cli import main
from whitespace.models import Post
from whitespace.storage import Database


@pytest.mark.parametrize("language_args", [[], ["--languages", "english"]])
def test_rules_skip_non_english_without_deleting_posts(
    monkeypatch, tmp_path, capsys, language_args
):
    from whitespace.extract import nlp

    monkeypatch.setenv("WHITESPACE_DATA_DIR", str(tmp_path))
    db = Database(tmp_path / "whitespace.sqlite")
    for i, lang in enumerate(("english", "russian")):
        db.upsert_post(Post("s", str(i), "u", f"Review {i}", i, lang=lang))
    for row in db.conn.execute("SELECT id, lang FROM post").fetchall():
        db.set_flags(row["id"], row["lang"], {"is_dup": False, "is_low_effort": False})
    db.conn.close()

    seen = []
    monkeypatch.setattr(nlp, "build_nlp", lambda index: None)
    monkeypatch.setattr(
        nlp, "RuleExtractor", lambda model, index: lambda text: seen.append(text) or []
    )
    assert main(["extract", "--extractor", "rules", "--prefilter", "all", *language_args]) == 0
    output = capsys.readouterr()
    assert seen == ["Review 0"]
    assert "'non_english': 1" in output.out
    assert "skipped, not deleted" in output.err
    db = Database(tmp_path / "whitespace.sqlite")
    assert db.count("post") == 2
    db.conn.close()


def test_rules_reject_all_languages_before_redo(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("WHITESPACE_DATA_DIR", str(tmp_path))
    db = Database(tmp_path / "whitespace.sqlite")
    db.upsert_post(Post("s", "1", "u", "review", 1, lang="russian"))
    db.conn.execute("INSERT INTO want_item(post_id, want_summary) VALUES (1, 'existing want')")
    db.conn.commit()
    db.conn.close()

    assert main(["extract", "--extractor", "rules", "--languages", "all", "--redo"]) == 2
    assert "English only" in capsys.readouterr().err
    db = Database(tmp_path / "whitespace.sqlite")
    assert db.count("post") == 1 and db.count("want_item") == 1
    db.conn.close()
