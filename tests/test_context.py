import json

import pytest

from whitespace.context import enrich_context, niche_genres, resolve_genres, source_context
from whitespace.models import CatalogGame

STARDEW = CatalogGame(413150, "Stardew Valley", ["Indie", "RPG", "Simulation"])
HADES = CatalogGame(1145360, "Hades", ["Action", "Indie", "RPG"])


def _row(db, want_id):
    return db.conn.execute("SELECT * FROM want_item WHERE id=?", (want_id,)).fetchone()


def test_steam_review_want_is_game_specific_with_catalog_genres(db, add_want):
    db.upsert_catalog(STARDEW)
    w = add_want("1", game_appid=413150)
    stats = enrich_context(db)
    row = _row(db, w)
    assert row["scope"] == "game_specific" and row["context_appid"] == 413150
    assert json.loads(row["context_genres"]) == ["RPG", "Simulation"]
    assert row["source_context"] is None
    assert stats == {"rows": 1, "with_genres": 1, "unresolved": 0}


def test_genre_wide_want_uses_source_genre_map(db, add_want):
    w = add_want(
        "r1",
        request_type="genre_request",
        quote="Why doesn't anyone make a cozy roguelike?",
        source="reddit",
        meta={"subreddit": "roguelikes"},
    )
    enrich_context(db, {"reddit:roguelikes": ["Action", "RPG"]})
    row = _row(db, w)
    assert row["scope"] == "genre_wide" and row["context_appid"] is None
    assert row["source_context"] == "reddit:roguelikes"
    assert json.loads(row["context_genres"]) == ["Action", "RPG"]


def test_reference_want_inherits_genres_of_the_referenced_game(db, add_want):
    db.upsert_catalog(STARDEW)
    db.upsert_catalog(HADES)
    w = add_want(
        "1",
        request_type="games_like",
        quote="Looking for games like Hades but with co-op",
        game_appid=413150,
        referenced=[1145360],
    )
    enrich_context(db)
    row = _row(db, w)
    assert row["scope"] == "reference"
    assert json.loads(row["context_genres"]) == ["Action", "RPG"]   # Hades, not Stardew


def test_unresolved_genres_stay_null_and_fill_in_on_a_later_run(db, add_want):
    w = add_want("1", game_appid=999)                    # not in the catalog yet
    first = enrich_context(db)
    row = _row(db, w)
    assert row["scope"] == "game_specific" and row["context_genres"] is None
    assert first["unresolved"] == 1

    db.upsert_catalog(CatalogGame(999, "Late Game", ["Strategy"]))
    second = enrich_context(db)
    assert json.loads(_row(db, w)["context_genres"]) == ["Strategy"]
    assert second["with_genres"] == 1


def test_enrich_is_idempotent(db, add_want):
    db.upsert_catalog(STARDEW)
    add_want("1", game_appid=413150)
    enrich_context(db)
    assert enrich_context(db)["rows"] == 0               # fully resolved rows are not revisited


def test_resolve_genres_priority_depends_on_scope(db):
    db.upsert_catalog(STARDEW)
    db.upsert_catalog(HADES)
    src = {"reddit:x": ["Casual"]}
    args = (db, "game_specific", 413150, [1145360], "reddit:x", src)
    assert resolve_genres(*args) == ["RPG", "Simulation"]          # own game first
    args = (db, "reference", 413150, [1145360], "reddit:x", src)
    assert resolve_genres(*args) == ["Action", "RPG"]              # referenced first
    assert resolve_genres(db, "genre_wide", None, [], "reddit:x", src) == ["Casual"]
    assert resolve_genres(db, "genre_wide", None, [], None, src) == []


@pytest.mark.parametrize(
    ("source", "meta", "expected"),
    [
        ("steam_reviews", None, None),
        ("reddit", '{"subreddit": "roguelikes"}', "reddit:roguelikes"),
        ("example_forum", "{}", "example_forum"),
        ("reddit", "{}", "reddit"),
    ],
)
def test_source_context(source, meta, expected):
    assert source_context(source, meta) == expected


def test_non_niche_genres_are_filtered():
    assert niche_genres(["Indie", "RPG", "Early Access", "Free to Play", "Action"]) == [
        "RPG",
        "Action",
    ]


def test_game_with_only_non_niche_genres_stays_unresolved(db, add_want):
    db.upsert_catalog(CatalogGame(5, "Only Indie", ["Indie"]))
    w = add_want("1", game_appid=5)
    assert enrich_context(db)["unresolved"] == 1
    assert _row(db, w)["context_genres"] is None


def test_refresh_reapplies_current_rules_to_resolved_rows(db, add_want):
    db.upsert_catalog(CatalogGame(413150, "Stardew Valley", ["Indie", "RPG"]))
    w = add_want("1", game_appid=413150)
    # a row enriched by an older version, before the stoplist existed
    db.conn.execute(
        "UPDATE want_item SET scope='game_specific', context_genres=? WHERE id=?",
        (json.dumps(["Indie", "RPG"]), w),
    )
    db.conn.commit()
    assert enrich_context(db)["rows"] == 0
    assert enrich_context(db, refresh=True)["rows"] == 1
    assert json.loads(_row(db, w)["context_genres"]) == ["RPG"]
