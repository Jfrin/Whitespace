"""Offline synthetic SQLite benchmark; it does not contact a model or clean reviews."""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

from whitespace.extract.run import extract_all
from whitespace.storage import Database


def synthetic_sqlite(rows: int, selection: int) -> dict:
    if rows < 1 or not 1 <= selection <= rows:
        raise ValueError("Rows must be positive and selection must be between 1 and rows")
    with tempfile.TemporaryDirectory(prefix="whitespace-benchmark-") as directory:
        path = Path(directory) / "benchmark.sqlite"
        db = Database(path)
        try:
            flags = json.dumps({"is_dup": False, "is_low_effort": False})
            started = time.monotonic()
            for start in range(0, rows, 10_000):
                db.conn.executemany(
                    "INSERT INTO post(source,source_id,text,lang,game_appid,flags) "
                    "VALUES (?,?,?,?,?,?)",
                    (
                        (
                            "synthetic",
                            str(i),
                            "synthetic review with no player data",
                            "en",
                            i % 100,
                            flags,
                        )
                        for i in range(start, min(start + 10_000, rows))
                    ),
                )
                db.conn.commit()
            insert_seconds = time.monotonic() - started
            started = time.monotonic()
            stats = extract_all(db, lambda text: [], limit=selection, prefilter="all")
            result = {
                "synthetic_rows": rows,
                "insert_seconds": round(insert_seconds, 3),
                "selection_seconds": round(time.monotonic() - started, 3),
                "selected": stats["candidates"],
                "database_bytes": path.stat().st_size,
                "scope": "pre-cleaned synthetic rows; no model calls or cleaning",
            }
            try:
                import resource

                # Linux reports KiB; macOS reports bytes. Windows has no resource module.
                import sys

                rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                result["peak_rss_bytes"] = int(rss if sys.platform == "darwin" else rss * 1024)
            except ImportError:
                result["peak_rss_bytes"] = None
            return result
        finally:
            db.conn.close()
