# Whitespace

Mine player demand ("I wish it had X") from public sources, cluster it into niches, and score
**demand vs. supply** to find under-served game ideas. Everything runs in Docker.

```
sources -> ingest -> raw store -> clean -> extract -> cluster -> gap score -> dashboard
```

## Quick start (Docker only)

```bash
cp .env.example .env        # set WHITESPACE_CONTACT at minimum
make build
make test                   # pytest + coverage gate, in the dev image
make lint                   # ruff check + format check
make ingest SOURCE=steam_reviews
make clean-posts
make stats
make dashboard              # http://localhost:8501
```

`make extract` needs `ANTHROPIC_API_KEY` in `.env`. Data lands in `./data` (gitignored).

## Layout

| Path | What |
|---|---|
| `config/sources.yaml` | Source registry: enabled flag, rate limit, ToS URL, `tos_reviewed` date |
| `ingest/` | `base.py` (rate limit, cursors, raw store, run ledger), `steam_reviews.py` (official API), `reddit.py` (official OAuth API), `forum.py` (Scrapling HTML) |
| `clean/` | Language, low-effort, near-dup, review-bomb detection. Flags, never deletes |
| `extract/` | Regex prefilter, LLM extraction with strict schema validation, name->appid resolver |
| `cluster/` | `embed.py` (UMAP + HDBSCAN, `ml` extra), `drift.py` (match clusters across runs) |
| `score/gap.py` | Versioned gap formula with all weights in one dataclass |
| `serve/app.py` | Streamlit dashboard |
| `tests/` | Offline tests: HTTP mocked with `httpx.MockTransport`, HTML from fixtures |

## Docker targets

- `dev`: editable install + pytest/ruff. Used by `make test|lint|fmt` and CI.
- `prod`: the `whitespace` CLI. `--build-arg WITH_BROWSERS=1` adds Playwright browsers for
  `DynamicFetcher`/`StealthyFetcher` sources (large; most sources don't need it).
- `dashboard`: Streamlit on :8501, reads `./data` read-only.

## CI

`.github/workflows/ci.yml` builds the dev image (layer-cached), runs ruff, ruff format check, and
pytest with a coverage gate (75%), then builds the prod and dashboard images and smoke-tests the CLI.
`pipeline.yml` is a manual run of ingest+clean; its schedule is commented out until you persist the
SQLite file between runs (see the TODO in the file).

## Adding a source

1. Read its ToS and robots.txt; add an entry to `config/sources.yaml` and fill `tos_reviewed`.
2. Prefer an official API. Otherwise subclass `SourceCrawler` (see `forum.py` for the Scrapling pattern).
3. Implement `fetch_since(cursor)` and a pure `parse(item)`. Save a small HTML/JSON fixture and write
   tests against it, so the parser is testable without the network.
4. Wire it in `cli.py::cmd_ingest`.

## Status: what's real vs. stubbed

Done and tested: ingest framework, Steam, Reddit, forum parser, cleaning, prefilter, extraction
(schema + retry), appid resolver, drift matching, gap formula, CLI, CI.

Not built yet (next up):
- [ ] `cluster` CLI command: wire `cluster_wants`, LLM naming of clusters, write `cluster`/`cluster_member`
- [ ] `score` CLI command: build `catalog_game` (Steam/itch), match niches to games, write `niche_score`
- [ ] Demand growth + distinct-community stats per cluster (inputs to `NicheDemand`)
- [ ] Dissatisfaction rate from negative reviews of matched games
- [ ] Dashboard: trend lines, representative quotes, closest existing games; weekly email report
- [ ] Persist the SQLite file between scheduled CI runs
- [ ] Gold-set evaluation for extraction precision/recall
- [ ] More sources (store reviews, itch.io, Kickstarter, YouTube) using the same crawler pattern

## Notes

- Use `StealthyFetcher` only where a site's ToS allows automated access. Never use it to bypass logins.
- Raw scraped data and the SQLite file stay out of git. Publish aggregates and short quotes only.
- `Dockerfile` browser install and the compose/CI files were written without running Docker in the
  authoring environment, so treat the first `make build` as the real test.
