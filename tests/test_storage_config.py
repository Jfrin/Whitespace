from whitespace.config import Settings, load_sources
from whitespace.storage import Database, RawStore


def test_cursor_roundtrip_and_update(db):
    assert db.get_cursor("s", "a") is None
    db.set_cursor("s", "a", 10)
    db.set_cursor("s", "a", 20)
    assert db.get_cursor("s", "a") == 20
    assert db.get_cursor("s", "b") is None


def test_raw_store_writes_untouched_bytes(tmp_path):
    p = RawStore(tmp_path).save("src", "weird/key:1", "héllo", "json")
    assert p.read_bytes() == "héllo".encode()
    assert "weird_key_1" in p.name


def test_db_file_created_in_nested_dir(tmp_path):
    Database(tmp_path / "a" / "b.sqlite")
    assert (tmp_path / "a" / "b.sqlite").exists()


def test_load_sources_splits_known_and_extra(tmp_path):
    f = tmp_path / "s.yaml"
    f.write_text("sources:\n  x:\n    enabled: true\n    min_interval_s: 3\n    appids: [1]\n")
    cfg = load_sources(f)["x"]
    assert cfg.enabled and cfg.min_interval_s == 3.0 and cfg.options == {"appids": [1]}


def test_settings_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("WHITESPACE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("WHITESPACE_CONTACT", "me@x.io")
    s = Settings.from_env()
    assert s.db_path == tmp_path / "whitespace.sqlite" and "me@x.io" in s.user_agent


def test_shipped_registry_parses_and_excludes_nothing_enabled_without_tos():
    cfg = load_sources("config/sources.yaml")
    assert {"steam_reviews", "reddit", "example_forum"} <= set(cfg)
    assert not cfg["reddit"].enabled and not cfg["example_forum"].enabled


def test_old_database_gets_new_columns_on_open(tmp_path):
    import sqlite3

    path = tmp_path / "old.sqlite"
    old = sqlite3.connect(path)  # schema as shipped before `referenced_games` existed
    old.execute(
        "CREATE TABLE want_item (id INTEGER PRIMARY KEY, post_id INTEGER, request_type TEXT,"
        " subject TEXT, want_summary TEXT, referenced_appids TEXT, intensity INTEGER,"
        " quote TEXT, confidence REAL, extractor_version TEXT)"
    )
    old.commit()
    old.close()
    db = Database(path)
    cols = {r["name"] for r in db.conn.execute("PRAGMA table_info(want_item)")}
    assert "referenced_games" in cols
    Database(path)  # opening again is a no-op, not an error
