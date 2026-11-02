from pathlib import Path

import pytest

from whitespace.backup import create_backup, restore_backup
from whitespace.cli import main
from whitespace.models import Post
from whitespace.storage import Database, RawStore


def test_backup_restore_preserves_data_and_rewrites_raw_paths(tmp_path):
    data = tmp_path / "data"
    db = Database(data / "whitespace.sqlite")
    db.upsert_post(Post("steam", "1", "url", "review text", 1))
    raw = RawStore(data / "raw")
    path = raw.save("steam", "1", "raw body", "json")
    raw_id = db.insert_raw("steam", "1", "url", path, "1", "42", 10)
    db.complete_raw(raw_id, [])
    raw.save("steam_appdetails", "42", "catalog body", "json")
    db.conn.close()

    backup, restored = tmp_path / "backup", tmp_path / "restored"
    assert create_backup(data, backup)["raw_files"] == 2
    assert restore_backup(backup, restored)["raw_files"] == 2
    copy = Database(restored / "whitespace.sqlite")
    assert copy.count("post") == 1
    stored_path = Path(copy.conn.execute("SELECT path FROM raw_fetch").fetchone()[0])
    assert stored_path.read_text() == "raw body"
    assert stored_path.is_relative_to(restored.resolve())
    with pytest.raises(FileExistsError):
        restore_backup(backup, restored)


def test_restore_rejects_corruption_without_creating_destination(tmp_path):
    data = tmp_path / "data"
    db = Database(data / "whitespace.sqlite")
    path = RawStore(data / "raw").save("steam", "1", "body", "json")
    raw_id = db.insert_raw("steam", "1", "url", path, "1")
    db.complete_raw(raw_id, [])
    db.conn.close()
    backup = tmp_path / "backup"
    create_backup(data, backup)
    next((backup / "raw").rglob("*.json")).write_text("corrupt")
    with pytest.raises(ValueError, match="checksum"):
        restore_backup(backup, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


def test_backup_retains_pending_content_for_missing_raw_file(tmp_path):
    data = tmp_path / "data"
    db = Database(data / "whitespace.sqlite")
    path = data / "raw" / "steam" / "day" / "1.json"
    db.insert_raw("steam", "1", "url", path, "1", "42", 1, b"pending")
    db.conn.close()
    backup, restored = tmp_path / "backup", tmp_path / "restored"
    create_backup(data, backup)
    restore_backup(backup, restored)
    copy = Database(restored / "whitespace.sqlite")
    row = copy.pending_raw("steam", "42")[0]
    assert row["pending_content"] == b"pending"
    assert Path(row["path"]).is_relative_to(restored.resolve())


def test_cli_backup_and_restore(monkeypatch, tmp_path):
    data = tmp_path / "data"
    db = Database(data / "whitespace.sqlite")
    db.conn.close()
    monkeypatch.setenv("WHITESPACE_DATA_DIR", str(data))
    assert main(["backup", str(tmp_path / "backup")]) == 0
    assert main(["restore", str(tmp_path / "backup"), str(tmp_path / "restored")]) == 0
    assert main(["restore", str(tmp_path / "backup"), str(tmp_path / "restored")]) == 1
