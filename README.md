# Whitespace

Mine player demand ("I wish it had X") from public sources, tie each want to the game and genre it
was said about, cluster wants into niches, and score **demand vs. supply** to find under-served game
ideas. Everything runs in Docker. No LLM, no API keys.

```
sources -> ingest -> raw store -> clean -> extract -> (context) -> cluster -> gap score -> dashboard
```

Status: ingest, clean, and extract work end to end. Catalog + context (game, genre, scope per want)
are written and waiting on your first run. Cluster, score, and the dashboard content are not built
yet. See `PLAN.md` for the full checklist.

## Quick start (Docker)

```powershell
copy .env.example .env      # set WHITESPACE_CONTACT=you@example.com
docker compose --profile dev build
docker compose run --rm test
docker compose --profile pipeline build app

docker compose run --rm app ingest steam_reviews
docker compose run --rm app clean
docker compose run --rm app extract
docker compose run --rm app catalog     # genres for every game wants refer to
docker compose run --rm app context     # scope + game + genres on every want
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
docker compose run --rm fmt         # auto-fix lint + formatting (run this first)
docker compose run --rm lint        # ruff check + format check
docker compose run --rm test        # pytest + coverage gate (75%)
```

Inspect what extraction produced:

```powershell
# random sample of extracted wants
docker compose run --rm --entrypoint python app -c "import sqlite3; c=sqlite3.connect('/app/data/whitespace.sqlite'); [print(r) for r in c.execute('select request_type, subject, want_summary, intensity from want_item order by random() limit 25')]"

# counts by request type
docker compose run --rm --entrypoint python app -c "import sqlite3; c=sqlite3.connect('/app/data/whitespace.sqlite'); [print(r) for r in c.execute('select request_type, count(*) from want_item group by 1 order by 2 desc')]"

# wants with the game and genres they were said about
docker compose run --rm --entrypoint python app -c "import sqlite3; c=sqlite3.connect('/app/data/whitespace.sqlite'); [print(r) for r in c.execute('select w.scope, c.name, w.context_genres, w.want_summary from want_item w left join catalog_game c on c.appid=w.context_appid order by random() limit 20')]"

# each want next to its source quote (judge precision)
docker compose run --rm --entrypoint python app -c "import sqlite3; c=sqlite3.connect('/app/data/whitespace.sqlite'); [print(r) for r in c.execute('select want_summary, substr(quote,1,120) from want_item order by random() limit 15')]"
```

## Layout

| Path | What |
|---|---|
| `config/sources.yaml` | Source registry: enabled flag, rate limit, ToS URL, `tos_reviewed` date |
| `config/games.yaml` | Curated games + aliases for the name gazetteer (optional bulk `data/apps.json`) |
| `ingest/` | `base.py` (rate limit, cursors, raw store, run ledger), `steam_reviews.py` (official API), `reddit.py` (official OAuth API), `forum.py` (Scrapling HTML) |
| `catalog.py` | Genres + store categories per appid from Steam's public `appdetails` endpoint (cached, rate limited) |
| `context.py` | Attaches scope, context game, and genres to every extracted want |
| `config/context.yaml` | Subreddit / forum -> genre map for posts not tied to one game |
| `clean/` | Language, low-effort, near-duplicate, review-bomb detection. Flags, never deletes |
| `extract/` | `prefilter.py` (regex candidates), `nlp.py` (spaCy rules), `rules.py` (pure-Python lexicons), `gazetteer.py` + `appid.py` (game names to appids), `schema.py` (validation) |
| `cluster/` | `embed.py` (UMAP + HDBSCAN, `ml` extra), `drift.py` (match clusters across runs), `text.py` (genre-prefixed text to embed) |
| `score/gap.py` | Versioned gap formula, all weights in one dataclass |
| `serve/app.py` | Streamlit dashboard |
| `gold.py` | Samples extractions to label by hand and scores precision / miss rate from the labeled CSV |
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

### Quality controls

The first real run showed the happy path works but leaked junk ("like this", "hour without reason",
"meh rating"). The second pass (`rules-v1`) adds:

- `games_like` only when `like` is a comparison and its object isn't a pronoun or generic noun
- a wider stoplist of non-design nouns, applied to targets and to with/without features
- `content` and `ux` subjects, plus progression/skills/loot as `mechanic`
- **confidence penalty**: items with no design subject lose 0.25 confidence. Rows are kept; consumers
  filter (`cluster/text.py` keeps `confidence >= 0.6`)

Extraction is **English-only**: posts whose language tag isn't English are skipped and counted as
`non_english` in the `extract` output (about a third of the first Steam sample). `complaint` items
("I wish this wasn't a roguelite") are emitted for negated wishes only; a general complaint channel
("no X", "can't X") is a pending decision in `PLAN.md`.

`confidence` is a heuristic score, not a probability. Remaining known gaps: wishes written as a
relative clause with no marker ("a game where you run a bakery") are missed, and lowercase or
abbreviated game names resolve less reliably than exact ones.

### Measuring quality (gold set)

Rules can only be tuned against labels, so there is tooling for it (no model involved):

```powershell
docker compose run --rm app gold-export --kind items --n 300    # extracted wants -> data/gold_items.csv
docker compose run --rm app gold-export --kind missed --n 100   # trigger posts with no want -> data/gold_missed.csv
# open the CSV, fill `correct` (items) or `should_extract` (missed) with y / n, save
docker compose run --rm app gold-score --kind items
docker compose run --rm app gold-score --kind missed
```

A third sample, `--kind untriggered`, draws cleaned posts that match **no** trigger phrase. Label
`contains_wish` y/n: if a real share say y, the prefilter is the bottleneck, and the labeled rows show
which phrasings to add (e.g. "needs more", "should have", "lacks", "no way to"). The CSVs land in
`.\data\` on your machine (the folder is mounted into the container). In Excel, save as
**CSV UTF-8** so accents survive.

`gold-score --kind items` reports precision overall, by request type / subject / scope, and above vs.
below the 0.6 confidence threshold (so you can see whether the threshold is earning its keep).
`--kind missed` reports how often a trigger post that produced nothing should have produced a want.
Sampling is seeded (`--seed`), so exports are reproducible.

### Re-running after rule changes

```powershell
docker compose --profile pipeline build app
docker compose run --rm app extract --redo       # deletes all wants and re-extracts from posts
docker compose run --rm app catalog
docker compose run --rm app context --refresh    # recompute scope + genres for every row
```

`--redo` only deletes `want_item` rows; posts, raw files, and the catalog are untouched.

## Context: every want knows what it was said about

A want like "faster progression system" is only meaningful with its game and genre, so
`whitespace context` attaches four fields to each `want_item`:

- **`context_appid`**: the game the post is about (a Steam review's own app). Kept separate from
  `referenced_appids`, the games mentioned for comparison ("games like Hades").
- **`context_genres`**: from the catalog (`whitespace catalog` fetches Steam genres per game). For
  "games like Y" wants the referenced games' genres come first (they define the niche); otherwise the
  context game's; otherwise the `config/context.yaml` map for that subreddit or forum.
- **`scope`**: `game_specific` ("this game should add X"), `genre_wide` ("why doesn't anyone make X"),
  or `reference` ("games like Y but with X"), from rules in `extract/rules.py`.
- **`source_context`**: where the post lives, e.g. `reddit:roguelikes`.

Order matters: run `extract`, then `catalog`, then `context`. Rows whose genres can't be resolved yet
stay NULL and are retried on the next `context` run, so it is safe to re-run. Clustering text is
built by `cluster/text.py` as `"action, rpg: faster progression system"`, so the same want in
different genres lands in different niches.

Known limits: Steam genres are coarse. Non-niche ones (Indie, Early Access, Free To Play, content
flags, non-game software) are filtered out when resolving. Finer user tags would come from a third
party and are not used.
The catalog endpoint is throttled by Steam; a 429 stops the run and the next run resumes.

## Adding a source

1. Read its ToS and robots.txt; add an entry to `config/sources.yaml` and fill `tos_reviewed`.
2. Prefer an official API. Otherwise subclass `SourceCrawler` (see `forum.py` for the Scrapling pattern).
3. Implement `fetch_since(cursor)` and a pure `parse(item)`. Save a small HTML/JSON fixture and write
   tests against it, so the parser is testable without the network.
4. Wire it in `cli.py::cmd_ingest`, and add a subreddit/board -> genre entry in `config/context.yaml`.

## Notes

- Use `StealthyFetcher` only where a site's ToS allows automated access. Never use it to bypass logins.
- Raw scraped data and the SQLite file stay out of git. Publish aggregates and short quotes only.
- Steam ingest currently pulls up to 20 pages x 100 negative reviews per game on the first run, then
  only newer reviews. A backfill mode is on the checklist.
- Schema changes ship with an entry in `storage.MIGRATIONS`, so older databases are upgraded on open.
  Delete `data/whitespace.sqlite` to start fresh at any time (raw files stay in `data/raw/`).
