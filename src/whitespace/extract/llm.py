"""LLM extraction behind a small protocol so tests never hit the network."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from typing import Protocol

from whitespace.extract.schema import WantItem, validate_item

EXTRACTOR_VERSION = "v0"

PROMPT = """You extract player wishes from a post about video games.
Return ONLY a JSON array (no prose, no markdown fences). Each element:
{{"request_type": one of feature_wish|genre_request|complaint|games_like|unmet_expectation,
 "subject": one of mechanic|setting|art_style|monetization|platform|difficulty|session_length|other,
 "want_summary": short normalized statement of the want,
 "referenced_games": [game names exactly as written],
 "intensity": 1 (casual mention) to 3 (strong, explicit demand),
 "quote": verbatim snippet under 200 characters supporting the item,
 "confidence": 0.0-1.0}}
Return [] if the post contains no real request. Post:
---
{text}
---"""


class Extractor(Protocol):
    def __call__(self, text: str) -> str: ...


def parse_items(raw: str) -> list[WantItem]:
    cleaned = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M).strip()
    data = json.loads(cleaned)
    if not isinstance(data, list):
        raise ValueError("expected a JSON array")
    items: list[WantItem] = []
    for d in data:
        try:
            items.append(validate_item(d))
        except ValueError:
            continue  # drop malformed items, keep the good ones
    return items


def extract_wants(text: str, call: Callable[[str], str], retries: int = 1) -> list[WantItem]:
    prompt = PROMPT.format(text=text[:4000])
    for attempt in range(retries + 1):
        try:
            return parse_items(call(prompt))
        except (json.JSONDecodeError, ValueError):
            if attempt == retries:
                return []
    return []


def anthropic_call(model: str | None = None) -> Callable[[str], str]:
    import anthropic  # needs the `llm` extra

    client = anthropic.Anthropic()
    model = model or os.environ.get("EXTRACT_MODEL", "claude-haiku-5-5")

    def call(prompt: str) -> str:
        msg = client.messages.create(
            model=model, max_tokens=1000, messages=[{"role": "user", "content": prompt}]
        )
        return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")

    return call
