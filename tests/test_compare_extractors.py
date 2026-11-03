import json
import sqlite3

import pytest

from whitespace.cli import main
from whitespace.extract.azure import AzureExtractor
from whitespace.extract.compare import compare_versions
from whitespace.extract.run import extract_all
from whitespace.extract.schema import WantItem
from whitespace.models import Post
from whitespace.storage import Database


def test_compare_versions_tracks_empty_outputs_and_excludes_filtered_and_unknown(db):
    for i in range(1, 7):
        db.upsert_post(Post("s", str(i), "url", f"review {i}", i, lang="en", game_appid=1))
        db.set_flags(i, "en", {"is_dup": False, "is_low_effort": False})

    class Extractor:
        def __init__(self, version, positive):
            self.version, self.positive = version, positive

        def __call__(self, text):
            return (
                [WantItem("feature_wish", "mechanic", "map", 2, "quote", 0.8)]
                if int(text.split()[-1]) in self.positive
                else []
            )

    extract_all(db, Extractor("rules-v4", {1, 2, 5, 6}), prefilter="all", compare_versions=True)
    extract_all(
        db, Extractor("azure-v3:test", {1, 3, 5, 6}), prefilter="all", compare_versions=True
    )
    db.conn.execute(
        "UPDATE extraction_attempt SET outcome='filtered' "
        "WHERE post_id=5 AND extractor_version='azure-v3:test'"
    )
    db.conn.execute(
        "UPDATE extraction_attempt SET outcome='unknown' "
        "WHERE post_id=6 AND extractor_version='azure-v3:test'"
    )
    db.conn.commit()
    result = compare_versions(db, "rules-v4", "azure-v3:test", examples=10)
    assert result["completed_detection"] == {
        "total": 4,
        "both": 1,
        "left_only": 1,
        "right_only": 1,
        "neither": 1,
    }
    assert result["shared_by_outcome"] == [
        {"left_outcome": "completed", "right_outcome": "completed", "n": 4},
        {"left_outcome": "completed", "right_outcome": "filtered", "n": 1},
        {"left_outcome": "completed", "right_outcome": "unknown", "n": 1},
    ]
    assert [row["id"] for row in result["mismatch_examples"]] == [2, 3]
    assert db.count("extraction_attempt") == 12 and db.count("want_item") == 8
    with pytest.raises(ValueError):
        compare_versions(db, "rules-v4", "rules-v4")


def test_legacy_attempts_are_unknown_after_migration(tmp_path):
    path = tmp_path / "old.sqlite"
    old = sqlite3.connect(path)
    old.execute(
        "CREATE TABLE extraction_attempt(post_id INTEGER, extractor_version TEXT, "
        "PRIMARY KEY(post_id, extractor_version))"
    )
    old.execute("INSERT INTO extraction_attempt VALUES (1,'old')")
    old.commit()
    old.close()
    db = Database(path)
    assert db.conn.execute("SELECT outcome FROM extraction_attempt").fetchone()[0] == "unknown"


def test_legacy_azure_outcomes_can_be_inferred_without_changing_the_database(db):
    for i in range(1, 4):
        db.upsert_post(Post("s", str(i), "url", f"review {i}", i, lang="en"))
        db.conn.execute(
            "INSERT INTO extraction_attempt(post_id,extractor_version,outcome) VALUES (?,?,?)",
            (i, "rules-v4", "completed"),
        )
        db.conn.execute(
            "INSERT INTO extraction_attempt(post_id,extractor_version,outcome) VALUES (?,?,?)",
            (i, "azure-v3:test", "unknown"),
        )
    db.conn.commit()
    endpoint = "https://resource.example"
    azure = AzureExtractor(db, "", endpoint, model="test", rpm=0, tpm=0)
    db.llm_cache_put(azure._key("review 1"), "test", '{"items": []}')
    db.llm_cache_put(azure._key("review 2"), "test", '{"items": [], "filtered": true}')
    azure.client.close()
    report = compare_versions(db, "rules-v4", "azure-v3:test", endpoint=endpoint)
    assert report["completed_detection"]["neither"] == 1
    assert report["versions"]["azure-v3:test"]["inferred_legacy_outcomes"] == {
        "completed": 1,
        "filtered": 1,
    }
    assert (
        db.conn.execute(
            "SELECT count(*) FROM extraction_attempt WHERE extractor_version='azure-v3:test' "
            "AND outcome='unknown'"
        ).fetchone()[0]
        == 3
    )
    assert report["shared_by_outcome"] == [
        {"left_outcome": "completed", "right_outcome": "completed", "n": 1},
        {"left_outcome": "completed", "right_outcome": "filtered", "n": 1},
        {"left_outcome": "completed", "right_outcome": "unknown", "n": 1},
    ]


def test_compare_cli_reports_versions_without_network(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("WHITESPACE_DATA_DIR", str(tmp_path))
    db = Database(tmp_path / "whitespace.sqlite")
    db.conn.execute("INSERT INTO post(source, source_id) VALUES ('s','1')")
    db.conn.executemany(
        "INSERT INTO extraction_attempt(post_id, extractor_version, outcome) VALUES (1,?,?)",
        [("left", "completed"), ("right", "completed")],
    )
    db.conn.commit()
    db.conn.close()
    assert main(["compare-extractors", "--left", "left", "--right", "right"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["completed_detection"]["neither"] == 1
