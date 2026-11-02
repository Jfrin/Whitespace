"""Run one pipeline job at a time without blocking the dashboard."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import yaml


def parse_appids(value: str) -> list[int]:
    """Parse comma- or whitespace-separated Steam app IDs."""
    parts = value.replace(",", " ").split()
    if (
        not parts
        or len(parts) > 100
        or any(not part.isascii() or not part.isdigit() for part in parts)
    ):
        raise ValueError("Enter 1–100 positive Steam app IDs, separated by commas or spaces.")
    ids = list(dict.fromkeys(int(part) for part in parts))
    if any(appid <= 0 or appid > 2**31 - 1 for appid in ids):
        raise ValueError("Steam app IDs must be positive integers.")
    return ids


def commands(stage: str, source: str, extractor: str, limit: int) -> list[list[str]]:
    """Build CLI arguments from the dashboard's supported options."""
    if source != "steam_reviews" or extractor not in {"azure", "rules"} or limit < 1:
        raise ValueError("Invalid pipeline options")
    stages = {
        "ingest": ["ingest", source],
        "clean": ["clean"],
        "extract": ["extract", "--extractor", extractor, "--limit", str(limit)],
        "catalog": ["catalog"],
        "context": ["context"],
        "steam-tags": ["steam-tags"],
        "review-metrics": ["review-metrics", "--refresh"],
    }
    if stage == "pipeline":
        return [stages[name] for name in ("ingest", "clean", "extract", "catalog", "context")]
    if stage not in stages:
        raise ValueError("Unknown pipeline stage")
    return [stages[stage]]


class JobRunner:
    """Shared runner: output and status remain available across UI reruns."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._status = "idle"
        self._lines: list[str] = []
        self._process: subprocess.Popen | None = None
        self._stop_requested = False

    def start(self, stages: list[list[str]], appids: list[int], env: dict[str, str]) -> bool:
        with self._lock:
            if self._status in {"running", "stopping"}:
                return False
            self._status = "running"
            self._lines = []
            self._stop_requested = False
        try:
            threading.Thread(target=self._run, args=(stages, appids, env), daemon=True).start()
        except Exception:
            with self._lock:
                self._status = "failed"
            raise
        return True

    def snapshot(self) -> tuple[str, str]:
        with self._lock:
            return self._status, "\n".join(self._lines)

    def stop(self) -> bool:
        """Stop the active stage and prevent any remaining stages from starting."""
        with self._lock:
            if self._status != "running":
                return False
            self._stop_requested = True
            self._status = "stopping"
            if self._process is not None and self._process.poll() is None:
                try:
                    self._process.terminate()
                except ProcessLookupError:
                    pass  # the stage exited between poll and terminate
            return True

    def _log(self, line: str) -> None:
        with self._lock:
            self._lines.append(line.rstrip())
            self._lines = self._lines[-1000:]

    def _run(self, stages: list[list[str]], appids: list[int], env: dict[str, str]) -> None:
        status = "completed"
        try:
            source_config = yaml.safe_load(Path("config/sources.yaml").read_text(encoding="utf-8"))
            source_config["sources"]["steam_reviews"]["appids"] = appids
            with tempfile.TemporaryDirectory(prefix="whitespace-") as directory:
                config_path = Path(directory) / "sources.yaml"
                config_path.write_text(yaml.safe_dump(source_config), encoding="utf-8")
                for stage in stages:
                    with self._lock:
                        if self._stop_requested:
                            status = "stopped"
                            break
                    self._log(f"\n> whitespace {' '.join(stage)}")
                    with self._lock:
                        if self._stop_requested:
                            status = "stopped"
                            break
                        self._process = subprocess.Popen(
                            [
                                sys.executable,
                                "-u",
                                "-m",
                                "whitespace.cli",
                                "--config",
                                str(config_path),
                                *stage,
                            ],
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            text=True,
                            encoding="utf-8",
                            errors="replace",
                            bufsize=1,
                            env=env,
                        )
                    try:
                        with self._process as process:
                            assert process.stdout is not None
                            for line in process.stdout:
                                self._log(line)
                            exit_code = process.wait()
                    finally:
                        with self._lock:
                            self._process = None
                    with self._lock:
                        stopping = self._stop_requested
                    if stopping:
                        status = "stopped"
                        break
                    if exit_code != 0:
                        self._log(f"Stage {stage[0]} failed; remaining stages were not run.")
                        status = "failed"
                        break
        except Exception as exc:
            self._log(f"Could not run pipeline: {exc}")
            status = "failed"
        finally:
            with self._lock:
                self._status = "stopped" if self._stop_requested else status
