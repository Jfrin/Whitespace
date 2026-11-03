"""Compare existing extractor results without making model requests."""

from __future__ import annotations

from collections import Counter

import httpx

from whitespace.extract.azure import AzureExtractor
from whitespace.storage import Database


def compare_versions(
    db: Database, left: str, right: str, examples: int = 10, endpoint: str | None = None
) -> dict:
    if not left or not right or left == right or examples < 0:
        raise ValueError("Choose two distinct versions and a non-negative example limit")
    versions = {}
    for version in (left, right):
        versions[version] = {
            "attempts_by_outcome": {
                row["outcome"]: row["n"]
                for row in db.conn.execute(
                    "SELECT outcome, COUNT(*) AS n FROM extraction_attempt "
                    "WHERE extractor_version=? GROUP BY outcome",
                    (version,),
                )
            },
            "items_without_attempt": db.conn.execute(
                "SELECT COUNT(*) FROM want_item w WHERE w.extractor_version=? AND NOT EXISTS "
                "(SELECT 1 FROM extraction_attempt a WHERE a.post_id=w.post_id "
                "AND a.extractor_version=w.extractor_version)",
                (version,),
            ).fetchone()[0],
            "inferred_legacy_outcomes": {},
        }
    exists_left = (
        "EXISTS(SELECT 1 FROM want_item w WHERE w.post_id=p.id "
        "AND w.extractor_version=a.extractor_version)"
    )
    exists_right = (
        "EXISTS(SELECT 1 FROM want_item w WHERE w.post_id=p.id "
        "AND w.extractor_version=b.extractor_version)"
    )
    rows = db.conn.execute(
        "SELECT p.id,p.source,p.lang,p.game_appid,p.text,a.outcome AS left_outcome, "
        f"b.outcome AS right_outcome,{exists_left} AS left_has,{exists_right} AS right_has "
        "FROM extraction_attempt a JOIN extraction_attempt b ON a.post_id=b.post_id "
        "JOIN post p ON p.id=a.post_id WHERE a.extractor_version=? AND b.extractor_version=? "
        "ORDER BY p.id",
        (left, right),
    )
    shared = Counter()
    counts = Counter({"total": 0, "both": 0, "left_only": 0, "right_only": 0, "neither": 0})
    inferred = {left: Counter(), right: Counter()}
    mismatches = []
    with httpx.Client() as client:
        resolvers = {}
        for version in (left, right):
            if endpoint and version.startswith("azure-v3:"):
                model = version.removeprefix("azure-v3:")
                long_posts = model.endswith(":long-v1")
                if long_posts:
                    model = model.removesuffix(":long-v1")
                extractor = AzureExtractor(
                    db,
                    "",
                    endpoint,
                    model=model,
                    client=client,
                    rpm=0,
                    tpm=0,
                    long_posts=long_posts,
                )
                if extractor.version == version:
                    resolvers[version] = extractor.cache_outcome
        for row in rows:
            outcomes = []
            for version, stored in ((left, row["left_outcome"]), (right, row["right_outcome"])):
                outcome = stored or "unknown"
                if outcome == "unknown":
                    resolved = None
                    if version in {"rules-v1", "rules-v2", "rules-v3"}:
                        resolved = "completed"
                    elif version in resolvers:
                        resolved = resolvers[version](row["text"] or "")
                    if resolved:
                        outcome = resolved
                        inferred[version][resolved] += 1
                outcomes.append(outcome)
            shared[tuple(outcomes)] += 1
            if outcomes != ["completed", "completed"]:
                continue
            counts["total"] += 1
            a, b = bool(row["left_has"]), bool(row["right_has"])
            counts[
                {
                    (True, True): "both",
                    (True, False): "left_only",
                    (False, True): "right_only",
                    (False, False): "neither",
                }[(a, b)]
            ] += 1
            if a != b and len(mismatches) < examples:
                wants = {}
                for version in (left, right):
                    wants[version] = [
                        {"summary": item["want_summary"], "quote": item["quote"]}
                        for item in db.conn.execute(
                            "SELECT want_summary, quote FROM want_item "
                            "WHERE post_id=? AND extractor_version=? ORDER BY id",
                            (row["id"], version),
                        )
                    ]
                mismatches.append(
                    {
                        "id": row["id"],
                        "source": row["source"],
                        "lang": row["lang"],
                        "game_appid": row["game_appid"],
                        "wants": wants,
                    }
                )
    for version in (left, right):
        versions[version]["inferred_legacy_outcomes"] = dict(inferred[version])
    return {
        "left": left,
        "right": right,
        "versions": versions,
        "shared_by_outcome": [
            {"left_outcome": a, "right_outcome": b, "n": n} for (a, b), n in sorted(shared.items())
        ],
        "completed_detection": dict(counts),
        "mismatch_examples": mismatches,
        "limitation": "Detection overlap is not precision or recall; filtered and unknown "
        "attempts are excluded, as are posts skipped by prefilters or language rules. "
        "Legacy outcomes for shared posts may be inferred from the current Azure cache or rules "
        "version; inferences are not stored and may not reflect later cache changes.",
    }
