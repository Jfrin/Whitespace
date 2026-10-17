"""Match new clusters to old ones by centroid similarity (pure python, no numpy needed)."""

from __future__ import annotations

import math


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def match_clusters(
    old: dict[int, list[float]], new: dict[int, list[float]], threshold: float = 0.8
) -> dict[int, int | None]:
    """For each new cluster id -> best old cluster id (or None if it's genuinely new)."""
    out: dict[int, int | None] = {}
    for nid, nvec in new.items():
        best_id, best = None, threshold
        for oid, ovec in old.items():
            s = cosine(nvec, ovec)
            if s >= best:
                best_id, best = oid, s
        out[nid] = best_id
    return out
