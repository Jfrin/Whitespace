"""Settings and the source registry."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class Settings:
    data_dir: Path = Path("data")
    contact: str = "unset@example.com"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "whitespace.sqlite"

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def user_agent(self) -> str:
        return f"whitespace/0.1 (research; contact: {self.contact})"

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            data_dir=Path(os.environ.get("WHITESPACE_DATA_DIR", "data")),
            contact=os.environ.get("WHITESPACE_CONTACT", "unset@example.com"),
        )


@dataclass(frozen=True)
class SourceConfig:
    name: str
    enabled: bool = False
    method: str = "api"
    min_interval_s: float = 2.0
    tos_reviewed: str | None = None
    options: dict[str, Any] = field(default_factory=dict)


def load_sources(path: str | Path = "config/sources.yaml") -> dict[str, SourceConfig]:
    raw = yaml.safe_load(Path(path).read_text()) or {}
    out: dict[str, SourceConfig] = {}
    for name, cfg in (raw.get("sources") or {}).items():
        known = {"enabled", "method", "min_interval_s", "tos_reviewed"}
        out[name] = SourceConfig(
            name=name,
            enabled=bool(cfg.get("enabled", False)),
            method=cfg.get("method", "api"),
            min_interval_s=float(cfg.get("min_interval_s", 2.0)),
            tos_reviewed=cfg.get("tos_reviewed"),
            options={k: v for k, v in cfg.items() if k not in known},
        )
    return out


def load_source_genres(path: str | Path = "config/context.yaml") -> dict[str, list[str]]:
    """Genre context for sources not tied to one game: {"reddit:<sub>" | <forum name>: [genres]}."""
    f = Path(path)
    if not f.exists():
        return {}
    raw = yaml.safe_load(f.read_text()) or {}
    mapping = raw.get("source_genres") or {}
    return {str(k): [str(g) for g in (v or [])] for k, v in mapping.items()}
