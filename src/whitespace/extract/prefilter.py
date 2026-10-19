"""Cheap regex first pass. Only candidate posts go through the slower spaCy pass."""

from __future__ import annotations

import re

PATTERNS = [
    r"\bi wish\b",
    r"\bwhy (doesn'?t|won'?t|does no ?one|hasn'?t) (anyone|anybody|someone)\b",
    r"\bwhy doesn'?t anyone make\b",
    r"\blooking for (a )?(game|games|something)\b",
    r"\bgames? like\b",
    r"\bif only\b",
    r"\bwould be (perfect|great|amazing) if\b",
    r"\bneeds? (a|an) [\w\- ]{0,25}(mode|option|system)\b",
    r"\bi('d| would) (love|kill) (for )?(a|an|to see)\b",
    r"\bwish (there was|someone would)\b",
    r"\bi (really |just |actually )?(want|need) (a|an|some)\b",
    r"\b(kill|die|dying) for\b",
    r"\bis there (a|any|an) (game|games)\b",
]
_COMPILED = [re.compile(p, re.I) for p in PATTERNS]


def _n(text: str) -> str:
    """Curly apostrophes -> straight, so "doesn’t" matches the same patterns as "doesn't"."""
    return text.replace("\u2019", "'").replace("\u2018", "'")


def match_patterns(text: str) -> list[str]:
    text = _n(text)
    return [p.pattern for p in _COMPILED if p.search(text)]


def is_candidate(text: str) -> bool:
    text = _n(text)
    return any(p.search(text) for p in _COMPILED)


def find_trigger(text: str) -> int | None:
    """Character offset of the earliest trigger phrase, or None."""
    text = _n(text)
    hits = [m.start() for p in _COMPILED if (m := p.search(text))]
    return min(hits) if hits else None


# Cheap cues that a post may complain about something missing. Deliberately broad: the real
# decision is made by the dependency rules, this only skips posts with no chance at all.
LACK_PATTERN = re.compile(
    r"\bno\b|\bwithout\b|\bmissing\b|\black(s|ed|ing)?\b|\bcan'?t\b|\bcannot\b|\bcould(n'?t| not)\b"
    r"|\bunable\b|\b(doesn|don|didn|won)'?t\b|\bnot (able|allowed|possible)\b|\bhard to\b"
    r"|\bdifficult to\b|\bimpossible to\b|\bunclear\b|\bnever (explain|tell|teach)"
    r"|\bnot enough\b|n't enough\b|\b(very|too|so) (little|few)\b",
    re.I,
)


def has_lack_cue(text: str) -> bool:
    return bool(LACK_PATTERN.search(_n(text)))


def is_any_candidate(text: str) -> bool:
    """Worth running the full extractor on: a wish trigger OR a complaint cue."""
    return is_candidate(text) or has_lack_cue(text)
