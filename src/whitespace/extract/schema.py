from __future__ import annotations

from dataclasses import dataclass, field

REQUEST_TYPES = {"feature_wish", "genre_request", "complaint", "games_like", "unmet_expectation"}
SUBJECTS = {
    "mechanic",
    "setting",
    "art_style",
    "monetization",
    "platform",
    "difficulty",
    "session_length",
    "other",
}


@dataclass(frozen=True)
class WantItem:
    request_type: str
    subject: str
    want_summary: str
    intensity: int
    quote: str
    confidence: float
    referenced_games: list[str] = field(default_factory=list)
    referenced_appids: list[int] = field(default_factory=list)


def validate_item(d: dict) -> WantItem:
    """Strict validation of one extracted item. Raises ValueError on anything off-schema."""
    try:
        item = WantItem(
            request_type=str(d["request_type"]),
            subject=str(d["subject"]),
            want_summary=str(d["want_summary"]).strip(),
            intensity=int(d["intensity"]),
            quote=str(d["quote"]).strip()[:300],
            confidence=float(d["confidence"]),
            referenced_games=[str(g) for g in d.get("referenced_games", [])],
            referenced_appids=[int(a) for a in d.get("referenced_appids", [])],
        )
    except (KeyError, TypeError) as exc:
        raise ValueError(f"bad extraction item: {exc!r}") from exc
    if item.request_type not in REQUEST_TYPES:
        raise ValueError(f"unknown request_type {item.request_type!r}")
    if item.subject not in SUBJECTS:
        raise ValueError(f"unknown subject {item.subject!r}")
    if not 1 <= item.intensity <= 3:
        raise ValueError("intensity must be 1-3")
    if not 0.0 <= item.confidence <= 1.0:
        raise ValueError("confidence must be 0-1")
    if not item.want_summary or not item.quote:
        raise ValueError("want_summary and quote are required")
    return item
