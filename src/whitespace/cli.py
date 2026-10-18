from __future__ import annotations

import argparse
import os
import sys

import httpx

from whitespace.config import Settings, load_source_genres, load_sources
from whitespace.ingest.base import RateLimiter
from whitespace.storage import Database, RawStore


def _client(settings: Settings) -> httpx.Client:
    return httpx.Client(headers={"User-Agent": settings.user_agent}, timeout=30.0)


def cmd_ingest(args: argparse.Namespace, settings: Settings) -> int:
    sources = load_sources(args.config)
    cfg = sources.get(args.source)
    if cfg is None or not cfg.enabled:
        print(f"source {args.source!r} is missing or disabled in {args.config}", file=sys.stderr)
        return 2
    db, raw = Database(settings.db_path), RawStore(settings.raw_dir)
    limiter = RateLimiter(cfg.min_interval_s, jitter_s=0.5)
    with _client(settings) as client:
        if args.source == "steam_reviews":
            from whitespace.ingest.steam_reviews import SteamReviewsCrawler

            for appid in cfg.options["appids"]:
                s = SteamReviewsCrawler(db, raw, limiter, appid, client).run()
                print(f"steam {appid}: {s}")
        elif args.source == "reddit":
            from whitespace.ingest.reddit import RedditCrawler, get_token

            token = get_token(
                client, os.environ["REDDIT_CLIENT_ID"], os.environ["REDDIT_CLIENT_SECRET"]
            )
            for sub in cfg.options["subreddits"]:
                print(f"r/{sub}: {RedditCrawler(db, raw, limiter, sub, client, token).run()}")
        elif cfg.method == "scrapling":
            from whitespace.ingest.forum import ForumCrawler

            print(ForumCrawler(db, raw, limiter, args.source, cfg.options).run())
        else:
            print(f"no crawler wired for {args.source!r}", file=sys.stderr)
            return 2
    return 0


def cmd_clean(args: argparse.Namespace, settings: Settings) -> int:
    from whitespace.clean.run import clean_posts

    print(clean_posts(Database(settings.db_path)))
    return 0


def cmd_extract(args: argparse.Namespace, settings: Settings) -> int:
    from whitespace.extract.gazetteer import load_index
    from whitespace.extract.nlp import RuleExtractor, build_nlp
    from whitespace.extract.run import extract_all

    index = load_index(args.games, settings.data_dir / "apps.json")
    extractor = RuleExtractor(build_nlp(index), index)
    print(extract_all(Database(settings.db_path), extractor, limit=args.limit, redo=args.redo))
    return 0


def cmd_catalog(args: argparse.Namespace, settings: Settings) -> int:
    from whitespace.catalog import referenced_appids, refresh_catalog

    cfg = load_sources(args.config).get("steam_catalog")
    interval = cfg.min_interval_s if cfg else 1.6
    db, raw = Database(settings.db_path), RawStore(settings.raw_dir)
    appids = referenced_appids(db)
    with _client(settings) as client:
        stats = refresh_catalog(
            db,
            client,
            RateLimiter(interval, jitter_s=0.3),
            appids,
            raw=raw,
            max_age_days=args.max_age_days,
            limit=args.limit,
        )
    print(f"{len(appids)} appids referenced; {stats}")
    return 0


def cmd_context(args: argparse.Namespace, settings: Settings) -> int:
    from whitespace.context import enrich_context

    db = Database(settings.db_path)
    print(enrich_context(db, load_source_genres(args.context_config), refresh=args.refresh))
    return 0


def cmd_gold_export(args: argparse.Namespace, settings: Settings) -> int:
    from whitespace import gold

    db = Database(settings.db_path)
    if args.kind == "items":
        rows, fields = gold.sample_items(db, args.n, args.seed), gold.ITEM_FIELDS
        default = settings.data_dir / "gold_items.csv"
    else:
        rows, fields = gold.sample_missed(db, args.n, args.seed), gold.MISSED_FIELDS
        default = settings.data_dir / "gold_missed.csv"
    out = args.out or default
    gold.write_csv(out, fields, rows)
    label = "correct" if args.kind == "items" else "should_extract"
    print(f"wrote {len(rows)} rows to {out}")
    print(f"fill the `{label}` column with y or n, then run gold-score")
    return 0


def cmd_gold_score(args: argparse.Namespace, settings: Settings) -> int:
    import json

    from whitespace import gold

    default = settings.data_dir / ("gold_items.csv" if args.kind == "items" else "gold_missed.csv")
    rows = gold.read_csv(args.file or default)
    result = gold.score_items(rows) if args.kind == "items" else gold.score_missed(rows)
    print(json.dumps(result, indent=2))
    return 0


def cmd_stats(args: argparse.Namespace, settings: Settings) -> int:
    db = Database(settings.db_path)
    for t in ("raw_fetch", "post", "want_item", "catalog_game", "cluster", "niche_score"):
        print(f"{t:13} {db.count(t)}")
    print("-- want_item context --")
    for row in db.conn.execute(
        "SELECT COALESCE(scope, '(not set)') AS s, COUNT(*) AS n FROM want_item "
        "GROUP BY 1 ORDER BY 2 DESC"
    ):
        print(f"scope {row['s']:14} {row['n']}")
    with_genres = db.conn.execute(
        "SELECT COUNT(*) FROM want_item WHERE context_genres IS NOT NULL"
    ).fetchone()[0]
    print(f"with genres    {with_genres}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="whitespace")
    p.add_argument("--config", default="config/sources.yaml")
    sub = p.add_subparsers(dest="cmd", required=True)
    ing = sub.add_parser("ingest", help="pull new items from one source")
    ing.add_argument("source")
    ing.set_defaults(fn=cmd_ingest)
    sub.add_parser("clean", help="flag dups / low-effort / language").set_defaults(fn=cmd_clean)
    ext = sub.add_parser("extract", help="regex prefilter + spaCy rule extraction (no LLM)")
    ext.add_argument("--games", default="config/games.yaml")
    ext.add_argument("--limit", type=int, default=None)
    ext.add_argument("--redo", action="store_true", help="delete all wants and re-extract")
    ext.set_defaults(fn=cmd_extract)
    cat = sub.add_parser("catalog", help="fetch genres for every game wants refer to")
    cat.add_argument("--max-age-days", type=int, default=30)
    cat.add_argument("--limit", type=int, default=None, help="max games to fetch this run")
    cat.set_defaults(fn=cmd_catalog)
    ctx = sub.add_parser("context", help="attach game, genres, and scope to extracted wants")
    ctx.add_argument("--context-config", default="config/context.yaml")
    ctx.add_argument("--refresh", action="store_true", help="recompute every row")
    ctx.set_defaults(fn=cmd_context)
    ge = sub.add_parser("gold-export", help="sample rows to label by hand (CSV)")
    ge.add_argument("--kind", choices=["items", "missed"], default="items")
    ge.add_argument("--n", type=int, default=300)
    ge.add_argument("--seed", type=int, default=42)
    ge.add_argument("--out", default=None)
    ge.set_defaults(fn=cmd_gold_export)
    gs = sub.add_parser("gold-score", help="precision / miss rate from a labeled CSV")
    gs.add_argument("--kind", choices=["items", "missed"], default="items")
    gs.add_argument("--file", default=None)
    gs.set_defaults(fn=cmd_gold_score)
    sub.add_parser("stats", help="row counts").set_defaults(fn=cmd_stats)
    args = p.parse_args(argv)
    return args.fn(args, Settings.from_env())


if __name__ == "__main__":
    raise SystemExit(main())
