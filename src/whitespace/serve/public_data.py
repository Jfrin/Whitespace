"""Read only the explicitly public fields from a packaged demo dataset."""

from __future__ import annotations

import json
import math
from pathlib import Path

FIELDS = ("game", "genres", "request_type", "subject", "summary", "quote", "confidence")


def load_demo(path: Path) -> tuple[str, str, list[dict]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise ValueError("Invalid demo dataset")
    label, description = payload.get("label"), payload.get("description")
    if (
        not isinstance(label, str)
        or not label
        or not isinstance(description, str)
        or not description
    ):
        raise ValueError("Demo dataset needs a label and description")
    items = []
    for row in payload["items"]:
        if not isinstance(row, dict) or any(field not in row for field in FIELDS):
            raise ValueError("Demo item is missing a public field")
        item = {field: row[field] for field in FIELDS}
        if any(
            not isinstance(item[key], str) or not item[key].strip()
            for key in ("game", "request_type", "subject", "summary", "quote")
        ):
            raise ValueError("Demo item has an invalid text field")
        if not isinstance(item["genres"], list) or not all(
            isinstance(genre, str) and genre.strip() for genre in item["genres"]
        ):
            raise ValueError("Demo item has invalid genres")
        confidence = item["confidence"]
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (float, int))
            or not math.isfinite(confidence)
            or not 0 <= confidence <= 1
        ):
            raise ValueError("Demo item has invalid confidence")
        items.append(item)
    return label, description, items


def filter_items(
    items: list[dict], games: list[str], genres: list[str], request_types: list[str], minimum: float
) -> list[dict]:
    return [
        item
        for item in items
        if (not games or item["game"] in games)
        and (not genres or any(genre in item["genres"] for genre in genres))
        and (not request_types or item["request_type"] in request_types)
        and item["confidence"] >= minimum
    ]
