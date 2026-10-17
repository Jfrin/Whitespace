"""Game name -> Steam appid. The quiet failure point, so overrides come first."""

from __future__ import annotations

import difflib
import re


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", name.lower()).strip()


class AppIndex:
    def __init__(self, apps: dict[int, str], overrides: dict[str, int] | None = None) -> None:
        self._apps = dict(apps)
        self._raw_overrides = dict(overrides or {})
        self._by_name = {_norm(n): a for a, n in apps.items()}
        self._overrides = {_norm(k): v for k, v in (overrides or {}).items()}

    def resolve(self, name: str, cutoff: float = 0.88) -> int | None:
        key = _norm(name)
        if key in self._overrides:
            return self._overrides[key]
        if key in self._by_name:
            return self._by_name[key]
        match = difflib.get_close_matches(key, self._by_name, n=1, cutoff=cutoff)
        return self._by_name[match[0]] if match else None

    def patterns(self) -> list[tuple[str, int]]:
        """(surface form, appid) pairs for the NER gazetteer: official names + aliases."""
        out = [(name, appid) for appid, name in self._apps.items()]
        out += list(self._raw_overrides.items())
        return out
