"""Cheap regex first pass. Only candidates go to the (paid) LLM."""

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
]
_COMPILED = [re.compile(p, re.I) for p in PATTERNS]


def match_patterns(text: str) -> list[str]:
    return [p.pattern for p in _COMPILED if p.search(text)]


def is_candidate(text: str) -> bool:
    return any(p.search(text) for p in _COMPILED)
