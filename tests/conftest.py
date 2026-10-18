import json
from pathlib import Path

import pytest

from whitespace.ingest.base import RateLimiter
from whitespace.models import Post
from whitespace.storage import Database, RawStore

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def db():
    return Database(":memory:")


@pytest.fixture
def raw(tmp_path):
    return RawStore(tmp_path / "raw")


@pytest.fixture
def limiter():
    return RateLimiter(0.0)  # no waiting in tests


@pytest.fixture
def forum_html():
    return (FIXTURES / "forum.html").read_text()


@pytest.fixture
def add_want(db):
    """Insert a post plus one want_item attached to it. Returns the want_item id."""

    def _add(
        source_id,
        summary="photo mode",
        request_type="feature_wish",
        quote="I wish it had a photo mode",
        referenced=None,
        game_appid=None,
        source="steam_reviews",
        meta=None,
    ):
        post = Post(
            source, source_id, "u", f"text {source_id}", 1, game_appid=game_appid, meta=meta or {}
        )
        db.upsert_post(post)
        post_id = db.conn.execute(
            "SELECT id FROM post WHERE source=? AND source_id=?", (source, source_id)
        ).fetchone()["id"]
        cur = db.conn.execute(
            "INSERT INTO want_item(post_id, request_type, subject, want_summary, referenced_games, "
            "referenced_appids, intensity, quote, confidence, extractor_version) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                post_id,
                request_type,
                "other",
                summary,
                "[]",
                json.dumps(referenced or []),
                2,
                quote,
                0.8,
                "t",
            ),
        )
        db.conn.commit()
        return cur.lastrowid

    return _add
