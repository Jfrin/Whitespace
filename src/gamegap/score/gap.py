"""Supply-gap scoring. Pure functions; every weight lives in one versioned config."""

from __future__ import annotations

import math
from dataclasses import dataclass

FORMULA_VERSION = "v0"


@dataclass(frozen=True)
class Weights:
    growth_floor: float = -0.5
    growth_cap: float = 2.0
    community_bonus: float = 0.25  # per extra distinct community, capped below
    community_cap: int = 8
    recency_half_life_years: float = 3.0
    review_confidence_k: float = 200.0  # reviews needed before a score is fully trusted


DEFAULT_WEIGHTS = Weights()


@dataclass(frozen=True)
class NicheDemand:
    weighted_mentions: float  # intensity-weighted, review-bomb down-weighted
    growth_rate: float  # fractional change vs. previous window, e.g. 0.3 = +30%
    avg_intensity: float  # 1-3
    communities: int  # distinct sources/subreddits/forums
    dissatisfaction_rate: float  # 0-1: share of complaints saying existing games fall short


@dataclass(frozen=True)
class SupplyGame:
    review_score: float  # 0-1 positive ratio
    review_count: int
    age_years: float


def demand_score(d: NicheDemand, w: Weights = DEFAULT_WEIGHTS) -> float:
    growth = min(max(d.growth_rate, w.growth_floor), w.growth_cap)
    spread = 1 + w.community_bonus * (min(d.communities, w.community_cap) - 1)
    return math.log1p(d.weighted_mentions) * (1 + growth) * (d.avg_intensity / 2) * max(spread, 1)


def supply_pressure(games: list[SupplyGame], w: Weights = DEFAULT_WEIGHTS) -> float:
    """High when many well-reviewed, recent games already serve the niche."""
    total = 0.0
    for g in games:
        trust = g.review_count / (g.review_count + w.review_confidence_k)
        recency = 0.5 ** (g.age_years / w.recency_half_life_years)
        total += g.review_score * trust * (0.5 + 0.5 * recency)
    return total


def gap_score(d: NicheDemand, games: list[SupplyGame], w: Weights = DEFAULT_WEIGHTS) -> float:
    return demand_score(d, w) * (1 + d.dissatisfaction_rate) / (1 + supply_pressure(games, w))
