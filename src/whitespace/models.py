"""Core records shared across pipeline stages."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any


def sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def hash_author(name: str | None, salt: str = "whitespace") -> str | None:
    """Never store raw usernames; keep a salted hash for distinct-author counts."""
    return sha1(f"{salt}:{name}")[:16] if name else None


@dataclass(frozen=True)
class Post:
    source: str
    source_id: str
    url: str
    text: str
    created_at: int  # unix seconds
    author_hash: str | None = None
    lang: str | None = None
    game_appid: int | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def content_hash(self) -> str:
        return sha1(" ".join(self.text.lower().split()))


@dataclass(frozen=True)
class CatalogGame:
    """One game's storefront facts. `genres` are coarse (Indie, RPG); `tags` here are Steam
    store categories (Co-op, Multi-player), not user-voted tags."""

    appid: int
    name: str | None
    genres: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    release_date: str | None = None
    price: float | None = None
