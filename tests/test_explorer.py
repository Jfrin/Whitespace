import json
from pathlib import Path

import pytest

from whitespace.models import CatalogGame, Post
from whitespace.serve.explorer import (
    Filters,
    count,
    game_coverage,
    options,
    page,
    safe_source_url,
    string_list,
)
from whitespace.storage import Database


def populate(db):
    db.upsert_catalog(CatalogGame(1, "Same Name"))
    db.upsert_catalog(CatalogGame(2, "Same Name"))
    for i in range(130):
        appid = 1 if i % 2 == 0 else 2
        db.upsert_post(
            Post("steam_reviews", str(i), "https://example.com", f"review {i}", i, game_appid=appid)
        )
        post_id = db.conn.execute("SELECT id FROM post WHERE source_id=?", (str(i),)).fetchone()[0]
        db.conn.execute(
            "INSERT INTO want_item(post_id, want_summary, quote, subject, request_type, "
            "confidence, context_genres, referenced_games) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                post_id,
                f"want {i}",
                "quote 100%",
                "ux" if i % 2 == 0 else "mechanic",
                "feature_wish" if i % 2 == 0 else "complaint",
                0.9 if i % 2 == 0 else None,
                json.dumps(["Simulation", "Adventure"]) if i % 2 == 0 else "invalid",
                json.dumps(["Reference Game"]),
            ),
        )
    db.conn.commit()


def test_explorer_options_and_combined_filters_before_pagination(db):
    populate(db)
    choices = options(db.conn)
    assert choices["games"] == {1: "Same Name (1)", 2: "Same Name (2)"}
    assert choices["genres"] == ["Adventure", "Simulation"]
    filters = Filters([1], ["Simulation"], ["ux"], ["feature_wish"], "100%", 0.8)
    assert count(db.conn, Filters()) == 130
    assert count(db.conn, filters) == 65
    first, second = page(db.conn, filters, 1, 25), page(db.conn, filters, 2, 25)
    assert len(first) == len(second) == 25
    assert first[0]["want_summary"] == "want 128" and second[0]["want_summary"] == "want 78"
    assert first[0]["game_appid"] == 1 and string_list(first[0]["referenced_games"]) == [
        "Reference Game"
    ]
    assert count(db.conn, Filters(genres=["Adventure"], games=[2])) == 0
    assert count(db.conn, Filters(min_confidence=0.1)) == 65
    assert count(db.conn, Filters(search="%_")) == 0  # search is literal, not SQL LIKE


def test_missing_metadata_and_invalid_pages(db):
    db.upsert_post(Post("s", "1", "", "text", 1))
    db.conn.execute(
        "INSERT INTO want_item(post_id, want_summary, context_genres) VALUES (1, 'missing', ?)",
        ('{"not": "array"}',),
    )
    db.conn.commit()
    assert options(db.conn)["games"] == {} and options(db.conn)["genres"] == []
    assert count(db.conn, Filters()) == 1 and count(db.conn, Filters(genres=["RPG"])) == 0
    assert page(db.conn, Filters(), 1, 25)[0]["game_name"] is None
    assert string_list("invalid") == [] and string_list(None) == []
    with pytest.raises(ValueError):
        page(db.conn, Filters(), 0, 25)
    with pytest.raises(ValueError):
        page(db.conn, Filters(), 1, 101)


def test_coverage_shows_posts_without_wants_and_configured_games_without_posts(db):
    db.upsert_post(Post("steam_reviews", "1", "", "first", 1, game_appid=413150))
    db.upsert_post(Post("steam_reviews", "2", "", "second", 2, game_appid=1145360))
    db.conn.execute("INSERT INTO want_item(post_id, want_summary) VALUES (1, 'want')")
    db.conn.commit()
    coverage = {r["appid"]: r for r in game_coverage(db.conn, [413150, 570])}
    assert (coverage[413150]["posts"], coverage[413150]["wants"]) == (1, 1)
    assert (coverage[1145360]["posts"], coverage[1145360]["wants"]) == (1, 0)
    assert (coverage[570]["posts"], coverage[570]["wants"]) == (0, 0)
    assert 1145360 in options(db.conn)["games"]


@pytest.mark.parametrize(
    "url", [None, "", "javascript:alert(1)", "https://[invalid", "https://user:pass@example.com"]
)
def test_unsafe_source_urls_are_not_linked(url):
    assert safe_source_url(url) is None


def test_valid_source_url_is_linked():
    assert safe_source_url("https://example.com/review") == "https://example.com/review"


def test_dashboard_explorer_filters_and_pagination(monkeypatch, tmp_path):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("WHITESPACE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("WHITESPACE_UI_ENV_PATH", str(tmp_path / "missing.env"))
    db = Database(tmp_path / "whitespace.sqlite")
    populate(db)
    db.conn.close()
    app = AppTest.from_file(Path("src/whitespace/serve/app.py").resolve(), default_timeout=20).run()
    assert not app.exception
    assert any("130 matching" in c.value for c in app.caption)
    assert next(x for x in app.number_input if x.label == "Page").max == 6
    next(x for x in app.multiselect if x.label == "Subject").set_value(["ux"]).run()
    assert not app.exception and any("65 matching" in c.value for c in app.caption)
    next(x for x in app.text_input if x.label == "Search summaries and quotes").set_value(
        "want 2"
    ).run()
    assert not app.exception and any("matching extracted items" in c.value for c in app.caption)
    assert any(x.value == "want 2" for x in app.subheader)
