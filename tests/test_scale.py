"""Offline synthetic load check; timings are observations, not production guarantees."""

import json
import random
import time
import tracemalloc

from whitespace.clean.filters import is_near_duplicate, shingles
from whitespace.clean.run import clean_posts
from whitespace.extract.run import extract_all
from whitespace.models import sha1
from whitespace.storage import Database


def test_synthetic_10k_review_load(tmp_path):
    db = Database(tmp_path / "scale.sqlite")
    size = 10_000

    def reviews():
        previous = ""
        for i in range(size):
            if i % 100 == 1:
                text = previous + " bonus"
            else:
                text = " ".join(f"review{i}word{j}" for j in range(18))
            previous = text
            yield ("s", str(i), text, i, i % 100, sha1(text))

    db.conn.executemany(
        "INSERT INTO post(source, source_id, text, created_at, game_appid, content_hash) "
        "VALUES (?,?,?,?,?,?)",
        reviews(),
    )
    db.conn.commit()
    tracemalloc.start()
    started = time.perf_counter()
    counts = clean_posts(db)
    clean_seconds = time.perf_counter() - started
    started = time.perf_counter()
    stats = extract_all(db, lambda text: [], limit=200, prefilter="all", english_only=False)
    extract_seconds = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert counts["processed"] == size and counts["dup"] == 100
    assert stats["candidates"] == 200 and stats["scanned"] <= 300
    print(
        f"10k synthetic reviews: clean {clean_seconds:.2f}s, select/extract 200 "
        f"{extract_seconds:.2f}s, Python allocations peak {peak / 1024**2:.2f} MiB"
    )


def test_common_vocabulary_review_load(tmp_path):
    db = Database(tmp_path / "common.sqlite")
    rng = random.Random(17)
    vocabulary = [f"feature{i}" for i in range(1200)]
    previous = ""
    rows = []
    for i in range(1_000):
        if i % 17 == 1:
            text = previous + " extra"
        else:
            text = "this game really needs more " + " ".join(rng.sample(vocabulary, 15))
        previous = text
        rows.append(("s", str(i), text, i, sha1(text)))
    db.conn.executemany(
        "INSERT INTO post(source, source_id, text, created_at, content_hash) VALUES (?,?,?,?,?)",
        rows,
    )
    db.conn.commit()
    started = time.perf_counter()
    seen = []
    expected = []
    for _, _, text, _, _ in rows:
        expected.append(is_near_duplicate(text, seen))
        seen.append(shingles(text))
    baseline = time.perf_counter() - started
    started = time.perf_counter()
    counts = clean_posts(db)
    print(
        f"1k shared-vocabulary reviews: indexed clean {time.perf_counter() - started:.2f}s; "
        f"brute-force duplicate checks {baseline:.2f}s"
    )
    assert counts["processed"] == 1000 and counts["dup"] == 59
    actual = [
        json.loads(row[0])["is_dup"]
        for row in db.conn.execute("SELECT flags FROM post ORDER BY id")
    ]
    assert actual == expected


def test_synthetic_sqlite_benchmark_cli(capsys):
    from whitespace.cli import main

    assert main(["bench-sqlite", "--rows", "100", "--selection", "10"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["synthetic_rows"] == 100 and result["selected"] == 10
    assert "no model calls" in result["scope"]
    assert main(["bench-sqlite", "--rows", "1", "--selection", "2"]) == 2
