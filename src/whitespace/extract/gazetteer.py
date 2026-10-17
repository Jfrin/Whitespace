"""Build the game-name index (NER gazetteer + appid resolver) from files, no network."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from whitespace.extract.appid import AppIndex


def load_index(
    games_yaml: str | Path = "config/games.yaml", apps_json: str | Path | None = None
) -> AppIndex:
    """`games_yaml`: curated games + aliases. `apps_json`: optional {appid: name} bulk file
    (e.g. exported from Steam/IGDB). Single-word names from bulk lists are matched
    case-sensitively, so a long list of common words won't tag every sentence."""
    apps: dict[int, str] = {}
    overrides: dict[str, int] = {}
    if apps_json and Path(apps_json).exists():
        apps.update({int(k): v for k, v in json.loads(Path(apps_json).read_text()).items()})
    path = Path(games_yaml)
    if path.exists():
        for g in (yaml.safe_load(path.read_text()) or {}).get("games", []):
            apps[int(g["appid"])] = g["name"]
            for alias in g.get("aliases", []):
                overrides[alias] = int(g["appid"])
    return AppIndex(apps, overrides)
