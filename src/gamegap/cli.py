from __future__ import annotations

import argparse
import os
import sys

import httpx

from gamegap.config import Settings, load_sources
from gamegap.ingest.base import RateLimiter
from gamegap.storage import Database, RawStore


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
            from gamegap.ingest.steam_reviews import SteamReviewsCrawler

            for appid in cfg.options["appids"]:
                s = SteamReviewsCrawler(db, raw, limiter, appid, client).run()
                print(f"steam {appid}: {s}")
        elif args.source == "reddit":
            from gamegap.ingest.reddit import RedditCrawler, get_token

            token = get_token(
                client, os.environ["REDDIT_CLIENT_ID"], os.environ["REDDIT_CLIENT_SECRET"]
            )
            for sub in cfg.options["subreddits"]:
                print(f"r/{sub}: {RedditCrawler(db, raw, limiter, sub, client, token).run()}")
        elif cfg.method == "scrapling":
            from gamegap.ingest.forum import ForumCrawler

            print(ForumCrawler(db, raw, limiter, args.source, cfg.options).run())
        else:
            print(f"no crawler wired for {args.source!r}", file=sys.stderr)
            return 2
    return 0


def cmd_clean(args: argparse.Namespace, settings: Settings) -> int:
    from gamegap.clean.run import clean_posts

    print(clean_posts(Database(settings.db_path)))
    return 0


def cmd_extract(args: argparse.Namespace, settings: Settings) -> int:
    from gamegap.extract.llm import anthropic_call
    from gamegap.extract.run import extract_all

    print(extract_all(Database(settings.db_path), anthropic_call(), limit=args.limit))
    return 0


def cmd_stats(args: argparse.Namespace, settings: Settings) -> int:
    db = Database(settings.db_path)
    for t in ("raw_fetch", "post", "want_item", "cluster", "niche_score"):
        print(f"{t:12} {db.count(t)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="gamegap")
    p.add_argument("--config", default="config/sources.yaml")
    sub = p.add_subparsers(dest="cmd", required=True)
    ing = sub.add_parser("ingest", help="pull new items from one source")
    ing.add_argument("source")
    ing.set_defaults(fn=cmd_ingest)
    sub.add_parser("clean", help="flag dups / low-effort / language").set_defaults(fn=cmd_clean)
    ext = sub.add_parser("extract", help="regex prefilter + LLM extraction")
    ext.add_argument("--limit", type=int, default=None)
    ext.set_defaults(fn=cmd_extract)
    sub.add_parser("stats", help="row counts").set_defaults(fn=cmd_stats)
    args = p.parse_args(argv)
    return args.fn(args, Settings.from_env())


if __name__ == "__main__":
    raise SystemExit(main())
