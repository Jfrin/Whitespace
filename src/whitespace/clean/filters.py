"""Cleaning: language, low-effort, near-duplicate, review-bomb detection. All pure functions."""

from __future__ import annotations

import re
import statistics
from collections.abc import Iterable


def detect_language(text: str) -> str:
    """Uses `langdetect` if installed, else a crude ASCII heuristic ('und' = unknown)."""
    try:
        from langdetect import detect  # optional

        return detect(text)
    except Exception:
        letters = [c for c in text if c.isalpha()]
        if not letters:
            return "und"
        ascii_ratio = sum(c.isascii() for c in letters) / len(letters)
        return "en" if ascii_ratio > 0.9 else "und"


_LOW_EFFORT = re.compile(r"^(this )?(game )?(sucks|is (trash|bad|garbage)|bad|trash|10/10)\b", re.I)


_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]")


def is_low_effort(text: str, min_words: int = 8) -> bool:
    t = text.strip()
    # Chinese/Japanese have no spaces, so count roughly two characters per word-equivalent
    units = max(len(t.split()), len(_CJK.findall(t)) // 2)
    return units < min_words or bool(_LOW_EFFORT.match(t))


def shingles(text: str, k: int = 3) -> set[str]:
    words = re.findall(r"\w+", text.lower())
    return {" ".join(words[i : i + k]) for i in range(max(len(words) - k + 1, 1))}


def jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def is_near_duplicate(text: str, seen: Iterable[set[str]], threshold: float = 0.8) -> bool:
    sh = shingles(text)
    return any(jaccard(sh, s) >= threshold for s in seen)


def detect_review_bombs(
    daily_counts: dict[str, int], z: float = 4.0, min_count: int = 20
) -> list[str]:
    """Flag days whose volume is a robust outlier (median/MAD). Down-weight, don't delete."""
    if len(daily_counts) < 7:
        return []
    values = list(daily_counts.values())
    med = statistics.median(values)
    mad = statistics.median(abs(v - med) for v in values) or 1.0
    return sorted(
        day for day, n in daily_counts.items() if n >= min_count and 0.6745 * (n - med) / mad >= z
    )
