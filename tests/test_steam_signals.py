import json

import httpx
import pytest

from whitespace.ingest.steam_review_fields import review_metrics
from whitespace.models import CatalogGame, Post
from whitespace.serve.explorer import (
    Filters,
    count,
    engagement_text,
    options,
    page,
    review_engagement,
)
from whitespace.steam_metrics import _update, hydrate_review_metrics, refresh_review_metrics
from whitespace.steam_tags import parse_user_tags, refresh_user_tags
from whitespace.storage import RawStore

HTML = """<html><div class="glance_tags popular_tags" data-appid="42">
<a class="app_tag" href="/tags/en/Farming"> Farming Sim </a>
<a class="app_tag">Co-op</a><a class="app_tag"> farming sim </a>
<div class="app_tag add_button">+</div></div></html>"""


def client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_parse_user_defined_tags_excludes_controls_and_dedupes():
    assert parse_user_tags(HTML, 42) == ["Farming Sim", "Co-op"]
    with pytest.raises(ValueError, match="age check"):
        parse_user_tags('<div class="age_gate">Age check</div>', 42)
    with pytest.raises(ValueError, match="unavailable"):
        parse_user_tags(HTML, 123)


def test_tag_fetch_is_cached_and_keeps_categories_separate(db, limiter):
    db.upsert_catalog(CatalogGame(42, "Game", tags=["Multi-player"]))
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, text=HTML)

    with client(handler) as http:
        first = refresh_user_tags(db, http, limiter, [42])
        second = refresh_user_tags(db, http, limiter, [42])
    assert first["fetched"] == second["fresh"] == 1 and len(calls) == 1
    assert json.loads(db.conn.execute("SELECT tags FROM steam_user_tags").fetchone()[0]) == [
        "Farming Sim",
        "Co-op",
    ]
    assert json.loads(db.conn.execute("SELECT tags FROM catalog_game").fetchone()[0]) == [
        "Multi-player"
    ]


def test_tags_restricted_failed_and_rate_limited_do_not_destroy_old_tags(db, limiter):
    with client(lambda req: httpx.Response(200, text=HTML)) as http:
        refresh_user_tags(db, http, limiter, [42])
    with client(lambda req: httpx.Response(302, headers={"location": "/agecheck/app/42"})) as http:
        assert refresh_user_tags(db, http, limiter, [42], max_age_days=0)["restricted"] == 1
    row = db.conn.execute("SELECT tags, status, fetched_at FROM steam_user_tags").fetchone()
    assert json.loads(row["tags"]) == ["Farming Sim", "Co-op"]
    assert row["status"] == "restricted" and row["fetched_at"]
    with client(lambda req: httpx.Response(200, text="changed layout")) as http:
        assert refresh_user_tags(db, http, limiter, [42], max_age_days=0)["failed"] == 1
    with client(lambda req: httpx.Response(429)) as http:
        assert refresh_user_tags(db, http, limiter, [42, 43], max_age_days=0)["rate_limited"] == 1
    assert db.count("steam_user_tags") == 1


def test_review_field_validation_and_unknown_counts():
    metrics = review_metrics(
        {
            "votes_up": 34,
            "votes_funny": 1,
            "voted_up": False,
            "weighted_vote_score": "0.75",
            "comment_count": -1,
        }
    )
    assert metrics == {
        "votes_up": 34,
        "votes_funny": 1,
        "voted_up": False,
        "weighted_vote_score": 0.75,
    }
    assert engagement_text(metrics) == [
        "34 people found this review helpful",
        "1 person found this review funny",
        "Steam weighted helpfulness: 0.750",
        "Not recommended",
    ]
    assert review_engagement("reddit", json.dumps(metrics)) == {}
    assert review_engagement("steam_reviews", "invalid") == {}
    assert (
        review_metrics({"votes_up": True, "votes_funny": None, "weighted_vote_score": "nan"}) == {}
    )


def test_hydrate_recovers_old_raw_metrics_without_losing_existing_metadata(db, tmp_path):
    db.upsert_post(
        Post(
            "steam_reviews",
            "42-123",
            "url",
            "review",
            100,
            game_appid=42,
            meta={"votes_up": 2, "other": "keep"},
        )
    )
    review = {
        "recommendationid": "123",
        "votes_up": 34,
        "votes_funny": 1,
        "voted_up": False,
        "weighted_vote_score": 0.65,
    }
    raw = RawStore(tmp_path / "raw")
    path = raw.save("steam_reviews", "42-123", json.dumps(review), "json")
    raw_id = db.insert_raw("steam_reviews", "42-123", "url", path, "1", "42", 100)
    db.complete_raw(raw_id, [])
    assert hydrate_review_metrics(db)["updated"] == 1
    assert hydrate_review_metrics(db)["scanned"] == 0
    meta = json.loads(db.conn.execute("SELECT meta FROM post").fetchone()[0])
    assert meta["other"] == "keep" and meta["votes_up"] == 34 and meta["votes_funny"] == 1
    assert not _update(db, 42, {**review, "votes_up": 1}, "2000-01-01T00:00:00+00:00")
    assert json.loads(db.conn.execute("SELECT meta FROM post").fetchone()[0])["votes_up"] == 34


def test_hydration_progresses_across_commit_batches(db, tmp_path):
    raw = RawStore(tmp_path / "raw")
    for i in range(130):
        db.upsert_post(Post("steam_reviews", f"42-{i}", "url", "review", i, game_appid=42))
        path = raw.save(
            "steam_reviews",
            f"42-{i}",
            json.dumps(
                {
                    "recommendationid": str(i),
                    "votes_up": i,
                }
            ),
            "json",
        )
        raw_id = db.insert_raw("steam_reviews", f"42-{i}", "url", path, "1", "42", i)
        db.complete_raw(raw_id, [])
    assert hydrate_review_metrics(db)["updated"] == 130
    assert hydrate_review_metrics(db)["scanned"] == 0


def test_refresh_resumes_pages_and_does_not_move_ingest_cursor(db, limiter):
    for review_id in (1, 2):
        db.upsert_post(
            Post(
                "steam_reviews",
                f"42-{review_id}",
                "url",
                "review",
                review_id,
                game_appid=42,
                meta={"votes_up": 1, "other": "keep"},
            )
        )
    db.set_cursor("steam_reviews", "42", 200)
    pages = {
        "*": {
            "reviews": [{"recommendationid": "2", "votes_up": 34, "votes_funny": 1}],
            "cursor": "next",
        },
        "next": {"reviews": [{"recommendationid": "1", "votes_up": 4}]},
    }
    calls = []

    def handler(request):
        params = json.loads(request.url.params["input_json"])
        calls.append(params["cursor"])
        assert params["review_type"] == 0
        return httpx.Response(200, json={"response": pages[params["cursor"]]})

    with client(handler) as http:
        first = refresh_review_metrics(db, http, limiter, [42], max_pages=1)
        second = refresh_review_metrics(db, http, limiter, [42], max_pages=1)
    assert first["partial"] == 1 and second["partial"] == 0 and calls == ["*", "next"]
    assert db.get_cursor("steam_reviews", "42") == 200
    meta = json.loads(db.conn.execute("SELECT meta FROM post WHERE source_id='42-2'").fetchone()[0])
    assert meta["votes_up"] == 34 and meta["votes_funny"] == 1 and meta["other"] == "keep"


def test_refresh_keeps_checkpoint_on_rate_limit_or_invalid_response(db, limiter):
    db.upsert_post(Post("steam_reviews", "42-1", "url", "review", 1, game_appid=42))
    with client(
        lambda req: httpx.Response(
            200,
            json={
                "response": {
                    "reviews": [{"recommendationid": "1", "votes_up": 3}],
                    "cursor": "next",
                }
            },
        )
    ) as http:
        assert refresh_review_metrics(db, http, limiter, [42], max_pages=1)["partial"] == 1
    with client(lambda req: httpx.Response(429)) as http:
        assert refresh_review_metrics(db, http, limiter, [42])["rate_limited"] == 1
    assert db.conn.execute("SELECT token FROM steam_review_metric_cursor").fetchone()[0] == "next"
    with client(lambda req: httpx.Response(200, json={"response": {}})) as http:
        assert refresh_review_metrics(db, http, limiter, [42])["failed"] == 1
    assert db.conn.execute("SELECT token FROM steam_review_metric_cursor").fetchone()[0] == "next"


def test_explorer_filters_tags_and_shows_review_metrics(db, add_want):
    add_want("42-1", game_appid=42)
    db.conn.execute(
        "UPDATE post SET meta=? WHERE source_id='42-1'",
        (json.dumps({"votes_up": 34, "votes_funny": 1}),),
    )
    db.conn.execute(
        "INSERT INTO steam_user_tags VALUES (42, ?, 'now', 'now', 'ok', 'steam_store_page')",
        (json.dumps(["Farming Sim", "Co-op"]),),
    )
    db.conn.commit()
    assert options(db.conn)["user_tags"] == ["Co-op", "Farming Sim"]
    assert count(db.conn, Filters(user_tags=["Farming Sim"])) == 1
    assert count(db.conn, Filters(user_tags=["RPG"])) == 0
    item = page(db.conn, Filters(user_tags=["Co-op"]), 1, 10)[0]
    assert engagement_text(review_engagement(item["source"], item["post_meta"]))[:2] == [
        "34 people found this review helpful",
        "1 person found this review funny",
    ]


def test_engagement_sort_handles_missing_invalid_and_zero(db, add_want):
    for source_id in ("1", "2", "3", "4"):
        add_want(source_id, summary=f"want {source_id}", game_appid=42)
    for source_id, meta in (
        ("1", '{"votes_up": 0, "votes_funny": 10}'),
        ("2", '{"votes_up": 34, "votes_funny": 1}'),
        ("3", "not json"),
        ("4", '{"votes_up": true}'),
    ):
        db.conn.execute("UPDATE post SET meta=? WHERE source_id=?", (meta, source_id))
    db.conn.commit()
    helpful = page(db.conn, Filters(sort_by="helpful"), 1, 10)
    funny = page(db.conn, Filters(sort_by="funny"), 1, 10)
    assert [item["want_summary"] for item in helpful] == ["want 2", "want 1", "want 4", "want 3"]
    assert [item["want_summary"] for item in funny][:2] == ["want 1", "want 2"]
    with pytest.raises(ValueError, match="sort"):
        page(db.conn, Filters(sort_by="invalid"), 1, 10)


def test_cli_signals_commands_and_ui_actions(monkeypatch, tmp_path, limiter, capsys):
    from whitespace.cli import main
    from whitespace.serve.jobs import commands
    from whitespace.storage import Database

    monkeypatch.setenv("WHITESPACE_DATA_DIR", str(tmp_path))
    config = tmp_path / "sources.yaml"
    config.write_text(
        "sources:\n  steam_reviews:\n    enabled: true\n    appids: [42]\n", encoding="utf-8"
    )
    db = Database(tmp_path / "whitespace.sqlite")
    db.upsert_post(Post("steam_reviews", "42-1", "url", "review", 1, game_appid=42))
    db.conn.close()
    monkeypatch.setattr("whitespace.cli.RateLimiter", lambda *args, **kwargs: limiter)

    def handler(request):
        if request.url.host == "store.steampowered.com":
            return httpx.Response(200, text=HTML)
        return httpx.Response(
            200,
            json={
                "response": {
                    "reviews": [{"recommendationid": "1", "votes_up": 34, "votes_funny": 1}]
                }
            },
        )

    monkeypatch.setattr("whitespace.cli._client", lambda settings: client(handler))
    assert main(["--config", str(config), "steam-tags"]) == 0
    assert main(["--config", str(config), "review-metrics", "--refresh"]) == 0
    db = Database(tmp_path / "whitespace.sqlite")
    assert db.count("steam_user_tags") == 1
    assert json.loads(db.conn.execute("SELECT meta FROM post").fetchone()[0])["votes_funny"] == 1
    assert "Steam review metrics" in capsys.readouterr().out
    assert commands("steam-tags", "steam_reviews", "rules", 1) == [["steam-tags"]]
    assert commands("review-metrics", "steam_reviews", "rules", 1) == [
        ["review-metrics", "--refresh"]
    ]
