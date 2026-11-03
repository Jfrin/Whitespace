from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import sys
import time
from dataclasses import asdict

import httpx

from whitespace.config import Settings, load_source_genres, load_sources
from whitespace.ingest.base import RateLimiter
from whitespace.storage import Database, RawStore, now_iso


def _client(settings: Settings) -> httpx.Client:
    return httpx.Client(headers={"User-Agent": settings.user_agent}, timeout=30.0)


def cmd_backup(args: argparse.Namespace, settings: Settings) -> int:
    from whitespace.backup import create_backup

    try:
        print(create_backup(settings.data_dir, args.destination))
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"Backup failed: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_restore(args: argparse.Namespace, settings: Settings) -> int:
    from whitespace.backup import restore_backup

    try:
        print(restore_backup(args.backup, args.destination))
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"Restore failed: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_bench_sqlite(args: argparse.Namespace, settings: Settings) -> int:
    from whitespace.bench import synthetic_sqlite

    try:
        print(json.dumps(synthetic_sqlite(args.rows, args.selection)))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


def cmd_compare_extractors(args: argparse.Namespace, settings: Settings) -> int:
    from whitespace.extract.compare import compare_versions

    db = Database(settings.db_path)
    try:
        print(
            json.dumps(
                compare_versions(
                    db,
                    args.left,
                    args.right,
                    args.examples,
                    endpoint=os.environ.get("AZURE_OPENAI_ENDPOINT"),
                ),
                indent=2,
            )
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    finally:
        db.conn.close()
    return 0


def cmd_ingest(args: argparse.Namespace, settings: Settings) -> int:
    sources = load_sources(args.config)
    cfg = sources.get(args.source)
    if cfg is None or not cfg.enabled:
        print(f"source {args.source!r} is missing or disabled in {args.config}", file=sys.stderr)
        return 2
    if args.max_pages is not None and args.max_pages < 1:
        print("--max-pages must be positive", file=sys.stderr)
        return 2
    if args.source not in {"steam_reviews", "reddit"} and (args.backfill or args.max_pages):
        print("--backfill and --max-pages are supported for Steam and Reddit", file=sys.stderr)
        return 2
    db, raw = Database(settings.db_path), RawStore(settings.raw_dir)
    limiter = RateLimiter(cfg.min_interval_s, jitter_s=0.5)
    with _client(settings) as client:
        if args.source == "steam_reviews":
            from whitespace.ingest.steam_reviews import SteamReviewsCrawler

            for appid in cfg.options["appids"]:
                s = SteamReviewsCrawler(
                    db,
                    raw,
                    limiter,
                    appid,
                    client,
                    max_pages=args.max_pages or int(cfg.options.get("max_pages", 20)),
                    backfill=args.backfill,
                ).run()
                args._metrics.setdefault("scopes", []).append(asdict(s))
                print(f"steam {appid}: {s}")
                if not s.complete:
                    print("Page limit reached; rerun ingest to resume this crawl.")
        elif args.source == "reddit":
            from whitespace.ingest.reddit import RedditCrawler, get_token

            token = get_token(
                client, os.environ["REDDIT_CLIENT_ID"], os.environ["REDDIT_CLIENT_SECRET"]
            )
            for sub in cfg.options["subreddits"]:
                s = RedditCrawler(
                    db,
                    raw,
                    limiter,
                    sub,
                    client,
                    token,
                    max_pages=args.max_pages or int(cfg.options.get("max_pages", 10)),
                    backfill=args.backfill,
                ).run()
                args._metrics.setdefault("scopes", []).append(asdict(s))
                print(f"r/{sub}: {s}")
                if not s.complete:
                    print("Page limit reached; rerun ingest to resume this crawl.")
        elif cfg.method == "scrapling":
            from whitespace.ingest.forum import ForumCrawler

            stats = ForumCrawler(db, raw, limiter, args.source, cfg.options).run()
            args._metrics.setdefault("scopes", []).append(asdict(stats))
            print(stats)
        else:
            print(f"no crawler wired for {args.source!r}", file=sys.stderr)
            return 2
    return 0


def cmd_clean(args: argparse.Namespace, settings: Settings) -> int:
    from whitespace.clean.run import clean_posts

    stats = clean_posts(Database(settings.db_path), refresh=args.refresh, min_words=args.min_words)
    args._metrics.update(stats)
    print(stats)
    return 0


def cmd_extract(args: argparse.Namespace, settings: Settings) -> int:
    from whitespace.extract.gazetteer import load_index
    from whitespace.extract.run import extract_all

    name = args.extractor or "azure"
    if name == "rules" and args.languages == "all":
        print(
            "The offline rules extractor supports English only; omit --languages all "
            "or use --extractor azure.",
            file=sys.stderr,
        )
        return 2
    if args.redo and args.compare_versions:
        print("--redo cannot be combined with --compare-versions", file=sys.stderr)
        return 2
    if name == "rules" and args.long_posts:
        print("--long-posts is supported only by the Azure extractor", file=sys.stderr)
        return 2
    index = load_index(args.games, settings.data_dir / "apps.json")
    db = Database(settings.db_path)
    if name == "azure":
        key = os.environ.get("AZURE_OPENAI_API_KEY")
        endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
        if not key or not endpoint:
            print(
                "Set AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT in .env "
                "(or use --extractor rules).",
                file=sys.stderr,
            )
            return 2
        from whitespace.extract.azure import DEFAULT_MODEL, AzureExtractor
        from whitespace.extract.quota import DEFAULT_RPM, DEFAULT_TPM

        model = args.model or os.environ.get("AZURE_OPENAI_DEPLOYMENT", DEFAULT_MODEL)
        try:
            rpm = (
                args.rpm
                if args.rpm is not None
                else float(os.environ.get("AZURE_OPENAI_RPM", str(DEFAULT_RPM)))
            )
            tpm = (
                args.tpm
                if args.tpm is not None
                else int(os.environ.get("AZURE_OPENAI_TPM", str(DEFAULT_TPM)))
            )
            batch = (
                args.batch_size
                if args.batch_size is not None
                else int(os.environ.get("AZURE_OPENAI_BATCH_SIZE", "8"))
            )
            input_rate, output_rate = args.input_price, args.output_price
            if input_rate is None and os.environ.get("AZURE_OPENAI_INPUT_PRICE_PER_MILLION"):
                input_rate = float(os.environ["AZURE_OPENAI_INPUT_PRICE_PER_MILLION"])
            if output_rate is None and os.environ.get("AZURE_OPENAI_OUTPUT_PRICE_PER_MILLION"):
                output_rate = float(os.environ["AZURE_OPENAI_OUTPUT_PRICE_PER_MILLION"])
        except ValueError:
            print("Azure rate limits and batch size must be numeric", file=sys.stderr)
            return 2
        if (
            not math.isfinite(rpm)
            or rpm < 0
            or tpm < 0
            or batch < 1
            or any(
                rate is not None and (not math.isfinite(rate) or rate < 0)
                for rate in (input_rate, output_rate)
            )
        ):
            print("rpm and tpm must be non-negative; batch size must be positive", file=sys.stderr)
            return 2
        print(
            f"azure {model}: {rpm:g} requests/min, {tpm} estimated tokens/min, "
            f"{batch} posts per request",
            file=sys.stderr,
        )
        with httpx.Client(timeout=120.0) as client:
            extractor = AzureExtractor(
                db,
                key,
                endpoint,
                model=model,
                client=client,
                rpm=rpm,
                tpm=tpm,
                index=index,
                batch_size=batch,
                long_posts=args.long_posts,
            )
            try:
                stats = extract_all(
                    db,
                    extractor,
                    limit=args.limit,
                    redo=args.redo,
                    prefilter=args.prefilter or "all",
                    english_only=(args.languages or "all") == "english",
                    compare_versions=args.compare_versions,
                )
            finally:
                if (
                    input_rate is not None
                    and output_rate is not None
                    and extractor.stats.get("usage_reported")
                ):
                    extractor.stats["known_estimated_cost_usd"] = (
                        extractor.stats["prompt_tokens"] * input_rate
                        + extractor.stats["completion_tokens"] * output_rate
                    ) / 1_000_000
                args._metrics["provider"] = dict(extractor.stats)
        print(stats)
        print(f"azure {model}: {extractor.stats}")
        args._metrics.update(stats)
        args._metrics["provider"] = dict(extractor.stats)
        return 1 if stats.get("stopped") else 0
    from whitespace.extract.nlp import RuleExtractor, build_nlp

    print(
        "Offline rules support English only; posts tagged as other languages are skipped, "
        "not deleted.",
        file=sys.stderr,
    )
    extractor = RuleExtractor(build_nlp(index), index)
    stats = extract_all(
        db,
        extractor,
        limit=args.limit,
        redo=args.redo,
        prefilter=args.prefilter or "cue",
        english_only=True,
        compare_versions=args.compare_versions,
    )
    print(stats)
    args._metrics.update(stats)
    return 0


def cmd_catalog(args: argparse.Namespace, settings: Settings) -> int:
    from whitespace.catalog import referenced_appids, refresh_catalog

    sources = load_sources(args.config)
    cfg = sources.get("steam_catalog")
    interval = cfg.min_interval_s if cfg else 1.6
    db, raw = Database(settings.db_path), RawStore(settings.raw_dir)
    steam = sources.get("steam_reviews")
    appids = referenced_appids(db, steam.options.get("appids", []) if steam else [])
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
    print(f"{len(appids)} appids to catalog; {stats}")
    args._metrics.update(stats)
    return 0


def cmd_steam_tags(args: argparse.Namespace, settings: Settings) -> int:
    from whitespace.catalog import referenced_appids
    from whitespace.steam_tags import refresh_user_tags

    sources = load_sources(args.config)
    cfg = sources.get("steam_catalog")
    interval = cfg.min_interval_s if cfg else 1.6
    db = Database(settings.db_path)
    steam = sources.get("steam_reviews")
    appids = referenced_appids(db, steam.options.get("appids", []) if steam else [])
    try:
        with _client(settings) as client:
            stats = refresh_user_tags(
                db,
                client,
                RateLimiter(interval, jitter_s=0.5),
                appids,
                max_age_days=args.max_age_days,
                limit=args.limit,
            )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    args._metrics.update(stats)
    print(f"{len(appids)} games; Steam user-defined tags: {stats}")
    return 1 if stats["rate_limited"] or stats["failed"] else 0


def cmd_review_metrics(args: argparse.Namespace, settings: Settings) -> int:
    from whitespace.steam_metrics import hydrate_review_metrics, refresh_review_metrics

    if args.max_pages < 1:
        print("--max-pages must be positive", file=sys.stderr)
        return 2
    db = Database(settings.db_path)
    hydrated = hydrate_review_metrics(db)
    args._metrics["hydrated"] = hydrated
    print(f"Stored review metrics: {hydrated}")
    if args.refresh:
        sources = load_sources(args.config)
        cfg = sources.get("steam_reviews")
        interval = cfg.min_interval_s if cfg else 1.5
        appids = [
            row[0]
            for row in db.conn.execute(
                "SELECT DISTINCT game_appid FROM post WHERE source='steam_reviews' "
                "AND game_appid IS NOT NULL ORDER BY game_appid"
            )
        ]
        with _client(settings) as client:
            refreshed = refresh_review_metrics(
                db, client, RateLimiter(interval, jitter_s=0.5), appids, args.max_pages
            )
        args._metrics.update(refreshed)
        print(f"Steam review metrics: {refreshed}")
        if refreshed["partial"]:
            print("Page limit reached or refresh failed; rerun Review metrics to resume.")
        return 1 if refreshed["rate_limited"] or refreshed["failed"] else 0
    return 0


def cmd_context(args: argparse.Namespace, settings: Settings) -> int:
    from whitespace.context import enrich_context

    db = Database(settings.db_path)
    stats = enrich_context(db, load_source_genres(args.context_config), refresh=args.refresh)
    args._metrics.update(stats)
    print(stats)
    return 0


def cmd_gold_export(args: argparse.Namespace, settings: Settings) -> int:
    from whitespace import gold

    db = Database(settings.db_path)
    filename, fields, label = gold.KINDS[args.kind]
    sampler = {
        "items": gold.sample_items,
        "missed": gold.sample_missed,
        "untriggered": gold.sample_untriggered,
        "rules": gold.sample_rules_posts,
    }[args.kind]
    rows = sampler(db, args.n, args.seed)
    out = args.out or settings.data_dir / filename
    gold.write_csv(out, fields, rows)
    print(f"wrote {len(rows)} rows to {out}")
    print(f"fill the `{label}` column with y or n, then run gold-score --kind {args.kind}")
    return 0


def cmd_gold_score(args: argparse.Namespace, settings: Settings) -> int:
    import json

    from whitespace import gold

    filename = gold.KINDS[args.kind][0]
    rows = gold.read_csv(args.file or settings.data_dir / filename)
    if args.kind == "rules":
        from whitespace.extract.gazetteer import load_index
        from whitespace.extract.nlp import RuleExtractor, build_nlp

        index = load_index(args.games, settings.data_dir / "apps.json")
        result = gold.score_rules_posts(rows, RuleExtractor(build_nlp(index), index))
    else:
        scorer = {
            "items": gold.score_items,
            "missed": gold.score_missed,
            "untriggered": gold.score_untriggered,
        }[args.kind]
        result = scorer(rows)
    print(json.dumps(result, indent=2))
    return 0


def cmd_sample(args: argparse.Namespace, settings: Settings) -> int:
    """Print a random sample of extracted wants (no SQL quoting needed)."""
    db = Database(settings.db_path)
    where: list[str] = []
    params: list = []
    if args.lang == "non-english":
        where.append("lower(COALESCE(p.lang, '')) NOT IN ('', 'en', 'english', 'eng')")
    elif args.lang:
        where.append("lower(p.lang) = ?")
        params.append(args.lang.lower())
    if args.type:
        where.append("w.request_type = ?")
        params.append(args.type)
    sql = (
        "SELECT p.lang, w.request_type, w.subject, w.want_summary, w.confidence, "
        "substr(w.quote, 1, 90) AS q FROM want_item w JOIN post p ON p.id = w.post_id"
        + (" WHERE " + " AND ".join(where) if where else "")
        + " ORDER BY random() LIMIT ?"
    )
    params.append(args.n)
    for r in db.conn.execute(sql, params):
        print(
            f"{str(r['lang']):10} {r['request_type']:17} {r['subject']:13} "
            f"{r['want_summary'][:38]:38} {(r['confidence'] or 0):.2f}  {r['q']}"
        )
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
    print("-- posts by language: total / low-effort / duplicate --")
    for row in db.conn.execute(
        "SELECT COALESCE(lang, '?') AS l, COUNT(*) AS n, "
        "SUM(json_extract(flags, '$.is_low_effort')) AS low, "
        "SUM(json_extract(flags, '$.is_dup')) AS dup "
        "FROM post GROUP BY 1 ORDER BY 2 DESC LIMIT 15"
    ):
        print(f"{row['l']:14} {row['n']:5} {row['low'] or 0:5} {row['dup'] or 0:5}")
    print("-- wants by request type --")
    for row in db.conn.execute(
        "SELECT request_type AS t, COUNT(*) AS n, ROUND(AVG(confidence), 2) AS c "
        "FROM want_item GROUP BY 1 ORDER BY 2 DESC"
    ):
        print(f"{str(row['t']):18} {row['n']:5}  avg confidence {row['c']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="whitespace")
    p.add_argument("--config", default="config/sources.yaml")
    sub = p.add_subparsers(dest="cmd", required=True)
    backup = sub.add_parser("backup", help="back up an idle database and raw files")
    backup.add_argument("destination", help="new backup directory")
    backup.set_defaults(fn=cmd_backup)
    restore = sub.add_parser("restore", help="verify and restore into a new data directory")
    restore.add_argument("backup", help="backup directory")
    restore.add_argument("destination", help="new data directory; never overwrites")
    restore.set_defaults(fn=cmd_restore)
    bench = sub.add_parser("bench-sqlite", help="run an offline synthetic SQLite benchmark")
    bench.add_argument("--rows", type=int, default=10_000)
    bench.add_argument("--selection", type=int, default=1000)
    bench.set_defaults(fn=cmd_bench_sqlite)
    compare = sub.add_parser("compare-extractors", help="compare existing completed versions")
    compare.add_argument("--left", required=True, help="first extractor version")
    compare.add_argument("--right", required=True, help="second extractor version")
    compare.add_argument("--examples", type=int, default=10, help="maximum disagreement examples")
    compare.set_defaults(fn=cmd_compare_extractors)
    ing = sub.add_parser("ingest", help="pull new items from one source")
    ing.add_argument("source")
    ing.add_argument("--backfill", action="store_true", help="scan historical pages")
    ing.add_argument("--max-pages", type=int, default=None, help="page budget per scope per run")
    ing.set_defaults(fn=cmd_ingest)
    cln = sub.add_parser("clean", help="flag dups / low-effort / language")
    cln.add_argument("--refresh", action="store_true", help="re-flag every post")
    cln.add_argument("--min-words", type=int, default=8, help="shorter posts are low-effort")
    cln.set_defaults(fn=cmd_clean)
    ext = sub.add_parser("extract", help="extract wants (Azure Foundry or offline rules)")
    ext.add_argument(
        "--extractor",
        choices=["azure", "rules"],
        default=None,
        help="Azure (default), or offline rules (English only)",
    )
    ext.add_argument("--model", default=None, help="deployment or model id")
    ext.add_argument("--rpm", type=float, default=None, help="requests/min (0 disables throttling)")
    ext.add_argument(
        "--tpm", type=int, default=None, help="estimated tokens/min (0 disables limit)"
    )
    ext.add_argument("--input-price", type=float, default=None, help="USD per million input tokens")
    ext.add_argument(
        "--output-price", type=float, default=None, help="USD per million output tokens"
    )
    ext.add_argument("--batch-size", type=int, default=None, help="posts per request (1 = off)")
    ext.add_argument(
        "--languages",
        choices=["all", "english"],
        default=None,
        help="Azure defaults to all languages; offline rules support English only",
    )
    ext.add_argument("--prefilter", choices=["cue", "all"], default=None)
    ext.add_argument("--games", default="config/games.yaml")
    ext.add_argument("--limit", type=int, default=None)
    ext.add_argument("--redo", action="store_true", help="delete all wants and re-extract")
    ext.add_argument(
        "--long-posts",
        action="store_true",
        help="extract overlapping chunks of long Azure posts (may increase costs)",
    )
    ext.add_argument(
        "--compare-versions",
        action="store_true",
        help="process posts separately by extractor version without deleting prior wants",
    )
    ext.set_defaults(fn=cmd_extract)
    cat = sub.add_parser("catalog", help="fetch names and genres for configured and ingested games")
    cat.add_argument("--max-age-days", type=int, default=30)
    cat.add_argument("--limit", type=int, default=None, help="max games to fetch this run")
    cat.set_defaults(fn=cmd_catalog)
    tags = sub.add_parser("steam-tags", help="fetch public Steam user-defined game tags")
    tags.add_argument("--max-age-days", type=int, default=30)
    tags.add_argument("--limit", type=int, default=None)
    tags.set_defaults(fn=cmd_steam_tags)
    metrics = sub.add_parser("review-metrics", help="recover or refresh Steam review metrics")
    metrics.add_argument(
        "--refresh", action="store_true", help="refresh existing reviews from Steam"
    )
    metrics.add_argument("--max-pages", type=int, default=20, help="page budget per app per run")
    metrics.set_defaults(fn=cmd_review_metrics)
    ctx = sub.add_parser("context", help="attach game, genres, and scope to extracted wants")
    ctx.add_argument("--context-config", default="config/context.yaml")
    ctx.add_argument("--refresh", action="store_true", help="recompute every row")
    ctx.set_defaults(fn=cmd_context)
    ge = sub.add_parser("gold-export", help="sample rows to label by hand (CSV)")
    ge.add_argument("--kind", choices=["items", "missed", "untriggered", "rules"], default="items")
    ge.add_argument("--n", type=int, default=300)
    ge.add_argument("--seed", type=int, default=42)
    ge.add_argument("--out", default=None)
    ge.set_defaults(fn=cmd_gold_export)
    gs = sub.add_parser("gold-score", help="precision / miss rate from a labeled CSV")
    gs.add_argument("--kind", choices=["items", "missed", "untriggered", "rules"], default="items")
    gs.add_argument("--file", default=None)
    gs.add_argument("--games", default="config/games.yaml", help="game index for rules evaluation")
    gs.set_defaults(fn=cmd_gold_score)
    smp = sub.add_parser("sample", help="print a random sample of extracted wants")
    smp.add_argument("--n", type=int, default=30)
    smp.add_argument("--lang", default=None, help="'non-english' or a Steam language, e.g. russian")
    smp.add_argument("--type", default=None, help="request type, e.g. complaint, dislike")
    smp.set_defaults(fn=cmd_sample)
    sub.add_parser("stats", help="row counts, languages, request types").set_defaults(fn=cmd_stats)
    args = p.parse_args(argv)
    settings = Settings.from_env()
    if args.cmd not in {
        "ingest",
        "clean",
        "extract",
        "catalog",
        "context",
        "steam-tags",
        "review-metrics",
    }:
        return args.fn(args, settings)
    args._metrics = {}
    started, start_clock = now_iso(), time.monotonic()
    status = "failed"
    try:
        result = args.fn(args, settings)
        status = "completed" if result == 0 else "failed"
        if result == 0 and (
            args._metrics.get("partial")
            or any(not s["complete"] for s in args._metrics.get("scopes", []))
        ):
            status = "partial"
        return result
    except Exception as exc:
        args._metrics["error_type"] = type(exc).__name__
        raise
    finally:
        if settings.db_path.exists():
            elapsed = time.monotonic() - start_clock
            units = {
                "ingest": sum(s["new_posts"] for s in args._metrics.get("scopes", [])),
                "clean": args._metrics.get("processed"),
                "extract": args._metrics.get("candidates"),
                "catalog": args._metrics.get("fetched"),
                "context": args._metrics.get("rows"),
                "steam-tags": args._metrics.get("fetched"),
                "review-metrics": args._metrics.get(
                    "updated", args._metrics.get("hydrated", {}).get("updated")
                ),
            }[args.cmd]
            if units is not None and elapsed > 0:
                args._metrics["units_per_second"] = units / elapsed
            try:
                db = Database(settings.db_path)
                db.conn.execute(
                    "INSERT INTO stage_run(stage, started_at, finished_at, elapsed_seconds, "
                    "status, metrics) VALUES (?,?,?,?,?,?)",
                    (args.cmd, started, now_iso(), elapsed, status, json.dumps(args._metrics)),
                )
                db.conn.commit()
                db.conn.close()
            except (OSError, sqlite3.Error) as exc:
                print(f"Could not record stage metrics: {type(exc).__name__}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
