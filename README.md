# Whitespace

Mine player demand ("I wish it had X") from public sources, tie each want to the game and genre it
was said about, cluster wants into niches, and score **demand vs. supply** to find under-served game
ideas. Everything runs in Docker. No LLM, no API keys.

```
sources -> ingest -> raw store -> clean -> extract -> (context) -> cluster -> gap score -> dashboard
```

Status: ingest, clean, and extract work end to end. Context, cluster, score, and the dashboard
content are not built yet. See `PLAN.md` for the full checklist.

## Quick start (Docker)

```powershell
copy .env.example .env      # set WHITESPACE_CONTACT=you@example.com
docker compose --profile dev build
docker compose run --rm test
docker compose --profile pipeline build app

docker compose run --rm app ingest steam_reviews
docker compose run --rm app clean
docker compose run --rm app extract
docker compose run --rm app stats
```

Data lands in `./data/whitespace.sqlite` (raw responses in `./data/raw/`), both gitignored.
If you have `make`, `make build | test | lint | ingest SOURCE=... | extract | stats | dashboard`
wrap the same commands.

**`docker compose run` does not rebuild images.** After changing code, rebuild first:
`docker compose --profile pipeline build app`.

On Linux, the container runs as UID 1000. If it can't write `./data`: `sudo chown -R 1000:1000 data`.

## Test commands

```powershell
docker compose run --rm test        # pytest + coverage gate (75%)
docker compose run --rm lint        # ruff check + format check
docker compose run --rm fmt         # auto-fix lint + formatting
```

Inspect what extraction produced:

```powershell
# random sample of extracted wants
docker compose run --rm --entrypoint python app -c "import sqlite3; c=sqlite3.connect('/app/data/whitespace.sqlite'); [print(r) for r in c.execute('select request_type, subject, want_summary, intensity from want_item order by random() limit 25')]"

# counts by request type
docker compose run --rm --entrypoint python app -c "import sqlite3; c=sqlite3.connect('/app/data/whitespace.sqlite'); [print(r) for r in c.execute('select request_type, count(*) from want_item group by 1 order by 2 desc')]"

# each want next to its source quote (judge precision)
docker compose run --rm --entrypoint python app -c "import sqlite3; c=sqlite3.connect('/app/data/whitespace.sqlite'); [print(r) for r in c.execute('select want_summary, substr(quote,1,120) from want_item order by random() limit 15')]"
```

## Layout

| Path | What |
|---|---|
| `config/sources.yaml` | Source registry: enabled flag, rate limit, ToS URL, `tos_reviewed` date |
| `config/games.yaml` | Curated games + aliases for the name gazetteer (optional bulk `data/apps.json`) |
| `ingest/` | `base.py` (rate limit, cursors, raw store, run ledger), `steam_reviews.py` (official API), `reddit.py` (official OAuth API), `forum.py` (Scrapling HTML) |
| `clean/` | Language, low-effort, near-duplicate, review-bomb detection. Flags, never deletes |
| `extract/` | `prefilter.py` (regex candidates), `nlp.py` (spaCy rules), `rules.py` (pure-Python lexicons), `gazetteer.py` + `appid.py` (game names to appids), `schema.py` (validation) |
| `cluster/` | `embed.py` (UMAP + HDBSCAN, `ml` extra), `drift.py` (match clusters across runs) |
| `score/gap.py` | Versioned gap formula, all weights in one dataclass |
| `serve/app.py` | Streamlit dashboard |
| `storage.py` | SQLite schema, raw store, automatic migrations for older databases |
| `tests/` | Offline tests: HTTP mocked with `httpx.MockTransport`, HTML from fixtures |

## Docker targets

- `dev`: editable install + pytest/ruff. Used by `test`, `lint`, `fmt`, and CI.
- `prod`: the `whitespace` CLI. `--build-arg WITH_BROWSERS=1` adds Playwright browsers for
  `DynamicFetcher`/`StealthyFetcher` sources (large; most sources don't need it).
- `dashboard`: Streamlit on :8501, reads `./data` read-only.

## CI

`.github/workflows/ci.yml` builds the dev image (layer-cached), runs ruff, a format check, and pytest
with the coverage gate, then builds the prod and dashboard images and smoke-tests the CLI.
`pipeline.yml` is a manual ingest+clean run; its schedule stays commented out until the SQLite file
is persisted between runs (see the TODO in the file).

## How extraction works (no LLM)

`whitespace extract` turns a post into structured wants in four deterministic steps:

1. **Prefilter** (`prefilter.py`): regex triggers ("I wish", "kill for", "games like", ...).
2. **Dependency parse + markers** (`nlp.py`): spaCy finds the wanted noun, then `with X` /
   `without X` / `no X` phrases become feature tuples, with filler words stripped. One item per
   tuple: *"I'd kill for a farming sim ... without the energy timer garbage"* becomes
   `farming sim without energy timer` plus `farming sim with co-op`. A small lexicon catches what the
   parser misses (e.g. "my partner can play too" -> co-op). This replaces OpenIE/CoreNLP (needs Java)
   with the same idea built on the spaCy parse.
3. **Gazetteer NER** (`EntityRuler`): game names from `config/games.yaml` (+ optional
   `data/apps.json`) are tagged and resolved to Steam appids. Multi-word names match any case;
   single-word names match case-sensitively.
4. **Intensity** (`rules.py`): strong phrases or a strong VADER magnitude -> 3; moderate verbs
   ("wish", "looking for") -> 2; neutral phrasing -> 1. The request wording is stripped before
   scoring so "would be great if" doesn't count as emotion.

### Known problems (seen on real Steam data)

The first real run showed the happy path works but the rules leak junk:

- `games_like` items whose object is a pronoun or generic noun ("like this", "like it")
- negation items with non-design heads ("hour without reason", "deficiency without fix")
- one-word complaints or slang captured as a want ("meh rating", "shiz")
- partial or abbreviated game names ("Binding", "DBD") missing from the gazetteer
- wishes written as a relative clause with no marker are missed entirely

`confidence` is a heuristic score, not a probability. Build a hand-labeled gold set before trusting
the output; thresholds in `rules.py` should be tuned against it.

## Next: tie every want to a game and genre

Today a want like "faster progression system" is stored without knowing what it was said about.
The design (not built yet, tracked in `PLAN.md` section 5):

- **Context game** (the game being discussed) is kept separate from **referenced games** (games
  mentioned for comparison). `post.game_appid` already exists; it needs to flow into `want_item`.
- **Context genres** come from the catalog (Steam genres/tags) for the context game; for Reddit and
  forums from a subreddit/board -> genre map in config; for "games like X" from X's tags.
- **Scope** is classified by rules: `game_specific` ("this game should add X"), `genre_wide`
  ("why doesn't anyone make X"), or `reference` ("games like Y but with X").
- **Clustering** embeds `"<genres>: <want>"`, so "faster progression" in roguelikes and in 4X games
  become different niches, and each niche reports what is wanted and where.
- **Scoring** limits the supply join and the dissatisfaction rate to games that share the niche's
  genre and tags.

## Adding a source

1. Read its ToS and robots.txt; add an entry to `config/sources.yaml` and fill `tos_reviewed`.
2. Prefer an official API. Otherwise subclass `SourceCrawler` (see `forum.py` for the Scrapling pattern).
3. Implement `fetch_since(cursor)` and a pure `parse(item)`. Save a small HTML/JSON fixture and write
   tests against it, so the parser is testable without the network.
4. Wire it in `cli.py::cmd_ingest`, and add a subreddit/board -> genre entry for context.

## Notes

- Use `StealthyFetcher` only where a site's ToS allows automated access. Never use it to bypass logins.
- Raw scraped data and the SQLite file stay out of git. Publish aggregates and short quotes only.
- Steam ingest currently pulls up to 20 pages x 100 negative reviews per game on the first run, then
  only newer reviews. A backfill mode is on the checklist.
- Schema changes ship with an entry in `storage.MIGRATIONS`, so older databases are upgraded on open.
  Delete `data/whitespace.sqlite` to start fresh at any time (raw files stay in `data/raw/`).
