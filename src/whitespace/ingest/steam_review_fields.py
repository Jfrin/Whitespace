"""Validate public review metrics without turning missing values into zero."""

import math


def review_metrics(review: dict) -> dict:
    metrics = {}
    for key in ("votes_up", "votes_funny", "comment_count"):
        value = review.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            metrics[key] = value
    if isinstance(review.get("voted_up"), bool):
        metrics["voted_up"] = review["voted_up"]
    score = review.get("weighted_vote_score")
    if isinstance(score, (int, float, str)) and not isinstance(score, bool):
        try:
            score = float(score)
            if math.isfinite(score) and 0 <= score <= 1:
                metrics["weighted_vote_score"] = score
        except ValueError:
            pass
    author = review.get("author")
    if isinstance(author, dict):
        for key in ("playtime_forever", "playtime_at_review"):
            value = author.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                metrics[key] = value
    return metrics
