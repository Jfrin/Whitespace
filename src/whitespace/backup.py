"""Offline portable backups of the database and raw files.

Stop pipeline writers before backup; SQLite's snapshot alone cannot synchronize raw files.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from pathlib import Path


def _files(root: Path):
    if not root.exists():
        return
    with os.scandir(root) as entries:
        for entry in entries:
            path = Path(entry.path)
            if entry.is_symlink():
                raise ValueError(f"Symlink in raw storage: {path}")
            if entry.is_dir(follow_symlinks=False):
                yield from _files(path)
            elif entry.is_file(follow_symlinks=False):
                yield path


def _copy_hash(source: Path, target: Path | None = None) -> str:
    digest = hashlib.sha256()
    if target is not None:
        target.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as src:
        if target is None:
            while chunk := src.read(1024 * 1024):
                digest.update(chunk)
        else:
            with target.open("xb") as dst:
                while chunk := src.read(1024 * 1024):
                    digest.update(chunk)
                    dst.write(chunk)
    return digest.hexdigest()


def _relative(path: str | Path, root: Path) -> Path:
    try:
        return Path(path).resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"Raw path is outside the data directory: {path}") from exc


def _safe_file(root: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError(f"Invalid backup path: {relative}")
    target = root / path
    if target.is_symlink() or not target.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"Invalid backup path: {relative}")
    return target


def _open_readonly(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(path)
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)


def create_backup(data_dir: str | Path, destination: str | Path) -> dict[str, int]:
    data_dir, destination = Path(data_dir), Path(destination)
    raw = data_dir / "raw"
    if destination.resolve().is_relative_to(raw.resolve()):
        raise ValueError("Backup destination cannot be inside raw storage")
    source = _open_readonly(data_dir / "whitespace.sqlite")
    try:
        destination.mkdir(parents=True, exist_ok=False)
        snapshot = sqlite3.connect(destination / "whitespace.sqlite")
        try:
            source.backup(snapshot)
            if snapshot.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Database snapshot failed integrity check")
            snapshot.execute("CREATE TABLE backup_manifest(path TEXT PRIMARY KEY, sha256 TEXT)")
            count = 0
            for path in _files(raw):
                relative = path.relative_to(raw).as_posix()
                digest = _copy_hash(path, destination / "raw" / relative)
                snapshot.execute("INSERT INTO backup_manifest VALUES (?, ?)", (relative, digest))
                count += 1
            for row in snapshot.execute("SELECT id, path, pending_content FROM raw_fetch"):
                relative = _relative(row[1], raw).as_posix()
                exists = snapshot.execute(
                    "SELECT 1 FROM backup_manifest WHERE path=?", (relative,)
                ).fetchone()
                if not exists and row[2] is None:
                    raise FileNotFoundError(f"Missing raw response: {row[1]}")
                snapshot.execute("UPDATE raw_fetch SET path=? WHERE id=?", (relative, row[0]))
            snapshot.commit()
            return {"raw_files": count}
        finally:
            snapshot.close()
    finally:
        source.close()


def restore_backup(backup: str | Path, destination: str | Path) -> dict[str, int]:
    backup, destination = Path(backup), Path(destination)
    if destination.exists():
        raise FileExistsError(destination)
    snapshot = _open_readonly(backup / "whitespace.sqlite")
    try:
        if snapshot.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Backup database failed integrity check")
        count = 0
        for relative, digest in snapshot.execute("SELECT path, sha256 FROM backup_manifest"):
            if _copy_hash(_safe_file(backup / "raw", relative)) != digest:
                raise ValueError(f"Backup checksum mismatch: {relative}")
            count += 1
        for relative, pending in snapshot.execute("SELECT path, pending_content FROM raw_fetch"):
            _safe_file(backup / "raw", relative)
            if (
                pending is None
                and not snapshot.execute(
                    "SELECT 1 FROM backup_manifest WHERE path=?", (relative,)
                ).fetchone()
            ):
                raise ValueError(f"Missing raw response in backup: {relative}")
        destination.mkdir(parents=True, exist_ok=False)
        restored = sqlite3.connect(destination / "whitespace.sqlite")
        try:
            snapshot.backup(restored)
            for relative, digest in snapshot.execute("SELECT path, sha256 FROM backup_manifest"):
                if (
                    _copy_hash(_safe_file(backup / "raw", relative), destination / "raw" / relative)
                    != digest
                ):
                    raise ValueError(f"Backup changed during restore: {relative}")
            for row_id, relative in restored.execute("SELECT id, path FROM raw_fetch"):
                restored.execute(
                    "UPDATE raw_fetch SET path=? WHERE id=?",
                    (str((destination / "raw" / relative).resolve()), row_id),
                )
            restored.execute("DROP TABLE backup_manifest")
            restored.commit()
        finally:
            restored.close()
        return {"raw_files": count}
    finally:
        snapshot.close()
