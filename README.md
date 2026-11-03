# Whitespace

A market-gap finder for game developers. It ingests public player feedback (Steam reviews today, with Reddit and forums planned), extracts structured wants and complaints with an LLM (Azure Foundry, any language), ties each one to the game and genre it was said about, clusters them into niches, and scores each niche by demand against existing supply. Every extracted want keeps its verbatim source quote and the model version that produced it.

```
sources -> ingest -> clean -> extract -> context -> cluster -> gap score -> dashboard
```

| Stage | What it does |
|---|---|
| **Ingest** | Pulls new posts incrementally. Official APIs where they exist (Steam, Reddit), Scrapling for HTML sources. Raw responses are stored untouched |
| **Clean** | Flags duplicates, low-effort posts, and review-bomb spikes (reversible, nothing is deleted) |
| **Extract** | Azure Foundry reads each post in any language and returns wants, complaints, and "games like X" references, each with a verbatim quote. A spaCy rules extractor is the offline fallback and baseline |
| **Context** | Attaches the game, its genres, and a scope (this game / the whole genre / "like X") to every want |
| **Cluster** | Groups similar wants into niches, with genre in the text so "faster progression" in a roguelike stays separate from the same wish in a 4X |
| **Gap score** | Demand (volume, growth, intensity, spread) against supply (similar games, their ratings, recency) and dissatisfaction |

Everything runs in Docker. SQLite for storage, Python 3.12, Scrapling for scraping, and Azure Foundry
for meaning extraction. Azure usage may incur charges. No usernames are stored and only public text is sent out.

**Status:** ingest, clean, extract, catalog, and context work end to end on Steam data, with gold-set
tooling to measure extraction quality. Clustering and gap scoring are not built yet.
See `PLAN.md` for the full checklist.

## Read-only demo (Docker)

Run `docker compose --profile public-demo up --build public-demo`, then open
**http://localhost:8502**. This separate explorer uses only fictional examples from
`demo/public.json`; its counts and quotes are not real player feedback. Filter by game, genre,
request type or confidence. It does not run the pipeline, use API credentials, or access `./data`.
The demo container packages only the public app and example data. It is not yet an Azure deployment.
Review any real dataset and source terms before publishing it.

## Local pipeline console (Docker)

First create `.env` from `.env.example` if it does not exist. Run
`docker compose --profile dashboard up --build dashboard` (or `make dashboard`), then open
**http://localhost:8501**. Enter Steam app IDs and your Azure endpoint, API key and deployment in
the sidebar, then click **Run full pipeline**. You can also run individual stages, monitor progress,
and view extracted wants. Select **Rules** for offline English extraction without an Azure key;
posts tagged as other languages are skipped, not deleted.

Use **Stop run** to terminate the current stage and skip the remaining stages. Committed work is
kept and the pipeline can be run again. On upgrade, ingest replays previously stored raw responses
once to recover any interrupted writes; missing raw files must be restored before it can continue.

The **Results** explorer searches all extracted wants and supports combined game, genre, Steam
user-defined tag, subject, request-type, and minimum-confidence filters. Sort by newest, helpful, or
funny; results are paginated with source quotes and links when available. Confidence is a heuristic,
not a probability. **Steam tags** explicitly fetches user-defined game tags from public store pages;
the catalog's existing tags are separate store categories. **Review metrics** recovers stored
review metadata and fetches current snapshots of existing reviews from Steam, up to 20 pages per
game per run. Rerun to resume partial refreshes. Counts can be missing or stale; helpful and funny
votes are not review recommendations or niche demand scores.
The **Games and extraction coverage** table shows posts and extracted items per Steam app, including
apps with no wants yet. Run **Catalog** to fetch missing game names and genres for configured and
ingested games. A Steam app ID in the settings or a fetched post does not mean that reviews
have been extracted. The extraction limit applies **across all games in a run**, not per app; limited
runs now interleave eligible posts across games. Completed posts are remembered even if they yield
zero items, so later runs progress to new posts. Re-run Extract to process more (Azure costs may
apply), then Catalog and Context. These changes do not retroactively add wants for unprocessed posts.

Settings apply to the browser session until you click **Save settings**, which saves Steam IDs in
`config/sources.yaml` and the remaining settings in `.env`. The Azure key is stored in plaintext;
keep `.env` private. Docker Compose must be recreated for other containers to receive new `.env`
values. The interface is bound to localhost and is intended for local use only; it has no user
authentication. Data is saved in `./data`.

## Command-line quick start (Docker)

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
Cleaning builds a persistent near-duplicate index, including a one-time pass over already-cleaned
posts after upgrading. This needs additional disk space; `clean --refresh` rebuilds the index.
Duplicates are checked across runs using the same 0.8 Jaccard threshold.
Steam reviews use the public `IUserReviewsService/GetAppReviews` API; no Steam API key is needed.
Anonymous calls have a lower rate limit and can return HTTP 429; wait and retry if that happens.
Steam ingest checkpoints after each complete page. It processes at most 20 pages per game per run
by default; `complete=False` means to rerun ingest to resume. The cursor advances only when that
crawl reaches its previous boundary or the API's end. Use `--max-pages N` to change the per-run
page budget. To recover older reviews missed by previous capped runs, start with
`docker compose run --rm app ingest steam_reviews --backfill`, then rerun without `--backfill`
until every game reports `complete=True`. Finish any already-in-progress incremental crawl before
starting a backfill. A later normal run picks up newer arrivals. Backfills
cover reviews the source still makes available, not necessarily every historical review; pagination
also depends on the source's cursor stability. Reddit uses the same checkpointing, subject to its
listing limits and terms. Steam's default review filter remains negative reviews only.
If you have `make`, `make build | test | lint | ingest SOURCE=... | extract | stats | dashboard`
wrap the same commands.

To fetch these optional signals explicitly:

```powershell
docker compose run --rm app steam-tags               # cache public user-defined store tags
docker compose run --rm app review-metrics           # recover metrics from stored raw reviews only
docker compose run --rm app review-metrics --refresh # refresh existing reviews, resumable
```

`steam-tags --max-age-days 0` refreshes cached tags; `review-metrics --max-pages N` changes the
per-game refresh page budget. Steam can cache anonymous review responses for up to 10 minutes.
Existing metrics and tags are retained on errors, and review refreshes do not alter ingest cursors.
Store pages requiring an age check or login are skipped, not bypassed. HTML layout and source
availability can change; review Steam's terms and robots.txt before collecting or publishing data.
These signals are not currently used in clustering or scoring.

### Backup and restore

Stop all pipeline writers before backing up: the SQLite snapshot and raw files cannot otherwise be
captured as one atomic snapshot. Backups include private raw data; store them securely.

```powershell
docker compose run --rm app backup /app/data/backups/snapshot-1
docker compose run --rm app restore /app/data/backups/snapshot-1 /app/data/restored-1
```

Both destinations must be new directories. Restore checks SQLite integrity and raw-file checksums,
rewrites raw paths for the destination, and never overwrites an existing directory. Configure
`WHITESPACE_DATA_DIR` to use the restored directory; do not move it after restoring. Failures can
leave a partial new destination, which must be inspected before manually removing it.

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

## Looking at results

```powershell
make stats                                         # counts, posts by language, wants by request type
make sample                                        # 30 random extracted wants
make sample LANGS=non-english                      # only non-English posts
make sample TYPE=dislike SAMPLE=50                 # one request type
```

Without make: `docker compose run --rm app sample --lang non-english --n 20`.

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
| `serve/app.py` | Local Streamlit pipeline console |
| `serve/public.py` | Separate read-only demo explorer; fictional data in `demo/public.json` |
| `gold.py` | Samples extractions to label by hand and scores precision / miss rate from the labeled CSV |
| `storage.py` | SQLite schema, raw store, automatic migrations for older databases |
| `tests/` | Offline tests: HTTP mocked with `httpx.MockTransport`, HTML from fixtures |

## Docker targets

- `dev`: editable install + pytest/ruff. Used by `test`, `lint`, `fmt`, and CI.
- `prod`: the `whitespace` CLI. `--build-arg WITH_BROWSERS=1` adds Playwright browsers for
  `DynamicFetcher`/`StealthyFetcher` sources (large; most sources don't need it).
- `dashboard`: local Streamlit pipeline console on :8501, reads and writes `./data`.
- `public-demo`: isolated Streamlit explorer with packaged fictional data, on :8502 locally.

## CI

`.github/workflows/ci.yml` builds the dev image (layer-cached), runs ruff, a format check, and pytest
with the coverage gate, then builds the prod and dashboard images and smoke-tests the CLI.
`pipeline.yml` is a manual ingest+clean run; its schedule stays commented out until the SQLite file
is persisted between runs (see the TODO in the file).

## Extraction with Azure Foundry (default)

Deploy `gpt-4.1-mini` in your Azure Foundry resource. Copy `.env.example` to `.env` and set
`AZURE_OPENAI_ENDPOINT` to the resource endpoint and `AZURE_OPENAI_API_KEY` to its key.
Set `AZURE_OPENAI_DEPLOYMENT` if your deployment has a different name. Availability, pricing,
and quotas depend on your Azure resource and deployment.

```powershell
docker compose --profile pipeline build app
docker compose run --rm app extract --limit 100            # try a small batch first
docker compose run --rm app extract                        # the rest
docker compose run --rm app extract --extractor rules      # offline spaCy fallback
```

How it behaves:

- **Batching:** by default 8 posts go in one request (`--batch-size`, `AZURE_OPENAI_BATCH_SIZE`; 1
  turns it off). Results are cached per post, so batched and single runs share the cache.
- **Progress:** the run selects posts incrementally and prints batch progress without loading the
  entire backlog or calculating a total up front. Ctrl+C is safe: finished batches are saved and
  cached responses make the re-run cheap. Each completed post is tracked by
  extractor version even if no want was found; uncached failed responses remain eligible for retry.
- **Model:** `gpt-4.1-mini` is the default deployment name; override with
  `AZURE_OPENAI_DEPLOYMENT` or `--model`.
- **Quotas:** configure `AZURE_OPENAI_RPM` (default 1212) and `AZURE_OPENAI_TPM` (default
  1212000) in the dashboard or `.env`; CLI flags `--rpm` and `--tpm` override them. These are
  per-process limits, not a shared quota manager. TPM uses a conservative estimate of request
  bytes plus maximum output tokens, not Azure's actual calculation or billed usage. Other clients,
  provider-side windows, and retries can still cause 429s. Set limits to your deployment's quota;
  higher limits can increase Azure spending. The extractor remains single-request-at-a-time.
- **Usage and cost:** Azure-reported input/output tokens, missing usage reports, retries, and cache
  hits are recorded per extraction run. Supply `--input-price` and `--output-price` in USD per
  million tokens (or `AZURE_OPENAI_INPUT_PRICE_PER_MILLION` and
  `AZURE_OPENAI_OUTPUT_PRICE_PER_MILLION`) for an estimate covering reported usage only; missing
  usage, discounts, and other charges make it incomplete. No price is assumed by default. Stage
  outcomes, timings, and counts are stored in SQLite's `stage_run`; abrupt termination may not be
  ledgered.
- **Long posts and comparisons:** `--long-posts` opts into overlapping chunks instead of the default
  3,000-character truncation and may increase charges. Add `--compare-versions` to process posts
  already extracted by other versions without deleting their wants. This migrates the want-item
  uniqueness constraint while preserving IDs; back up first. Results from different versions can
  include duplicate summaries, so filter by `extractor_version` before aggregating them. Chunk
  quality and cost on real reviews have not yet been evaluated.
- **Rate limits:** 429 and server errors are retried; if retries are exhausted the run stops cleanly
  (`stopped: 1`) and can be resumed. Responses are cached per post and deployment in `llm_cache`,
  so cached re-runs do not call the API.
- **Content filtering:** if Azure blocks a batch, posts are checked individually; blocked posts are
  skipped and cached as filtered while extraction continues. Counts appear in `filtered_posts` and
  `filtered_batches`. Other API errors still stop the run.
- **Every language by default.** The model reads Russian, Chinese, Portuguese, Turkish, etc. and
  writes `want_summary` in English, so a Russian "no checkpoints" and an English one cluster together.
  The first Steam sample was ~51% non-English, so this roughly doubles the data. `--languages english`
  restricts it (the spaCy rules extractor is always English-only).
- **Sends every cleaned post** by default (`--prefilter all`), because the LLM finds implicit
  demand the regexes can't. `--prefilter cue` sends only posts with a wish trigger or lack cue.
- **Dislikes are separate:** a post that only says "awful art style" or "HATE the fishing" becomes a
  `dislike` item (aspect only, confidence 0.4), not a vague "better X" want. Dislikes are kept for the
  dissatisfaction score and excluded from demand clustering. The prompt (`azure-v3`) also asks for
  specific summaries and sets confidence by actionability (0.9 concrete change, 0.7 area + direction,
  0.4 sentiment only), so the 0.6 filter means something.
- **Guards:** output is forced to a JSON schema; each item must pass the same strict validation as the
  rules path; and the model's `quote` must appear verbatim in the post or the item is dropped
  (`bad_quote` in the stats counts these). Markup (Steam BBCode) is stripped before sending.
- **Cleaning and CJK:** the low-effort filter used to count words by spaces, so Chinese/Japanese
  reviews (no spaces) were all dropped. It now counts characters for CJK. After upgrading run
  `clean --refresh` once to re-flag existing posts (`--min-words` changes the threshold, default 8).
- **What it sends:** only public review text, no usernames. Review your Azure deployment's data
  handling and pricing before sending data.
- **Comparing extractors:** every want records `extractor_version` (e.g. `azure-v3:gpt-4.1-mini`
  or `rules-v4`). Use `--compare-versions --prefilter all` for each extractor and restrict Azure to
  English for a shared comparison with the English-only rules. Then run:

  ```powershell
  docker compose run --rm app compare-extractors --left rules-v4 --right azure-v3:gpt-4.1-mini
  ```

  The report compares posts completed by both versions, including empty outputs; it separates
  content-filtered and legacy `unknown` attempts rather than treating them as true negatives. Where
  possible, legacy Azure outcomes are inferred from the current endpoint's cache and old rules
  outcomes from their versions; inferences are reported separately and are not saved. Missing or
  changed caches remain unknown. The dashboard's comparison option
  preserves both versions and its extractor-version filter helps avoid duplicate counts. Agreement
  is not precision or recall; evaluate a labeled sample. Azure runs may incur charges.

## Rules extractor (offline fallback)

`whitespace extract --extractor rules` turns a post into structured wants in deterministic steps:

The rules extractor processes long reviews in bounded windows and maps each quote back to a literal
slice of the original text, retaining punctuation, whitespace, and markup. Very long individual
sentences may still lose context at window boundaries. `rules-v4` adds targeted negation and
hypothetical safeguards; its quality has not been established on human-labeled real reviews.

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

Offline extraction is **English-only**: posts tagged as other languages are skipped and counted as
`non_english` in the `extract` output. They remain in storage and can be processed with Azure later.
Language filtering relies on the stored tag; missing tags are treated as English, so untagged or
incorrectly tagged posts may still be processed. `--extractor rules --languages all` is rejected.
`complaint` items
are emitted by a separate lack channel ("no X", "can't X", "lacks X", "not enough X"), written as the
thing that is missing in positive form ("no fast restarts" becomes `fast restart`) so they cluster
with wishes. Negated wishes ("I wish this wasn't a roguelite") stay `without roguelite`.

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

#### Evaluating offline rules on English posts

The `rules` gold set samples cleaned English-tagged posts whether or not they already have wants.
This avoids treating unprocessed posts as extractor misses. It includes posts without cue phrases,
so the score measures the default cue prefilter as well as the extractor.

```powershell
docker compose --profile pipeline build app
docker compose run --rm app gold-export --kind rules --n 300   # -> data/gold_rules.csv
# Label contains_want y/n for whether the full post contains actionable game feedback.
# Save as CSV UTF-8, then score without changing the database:
docker compose run --rm app gold-score --kind rules
```

The output reports post-level detection precision and recall, confusion counts, and IDs of false
positives and false negatives. A detected post may contain wrong or additional missed items, so
these are **not item-level precision or recall**. Label `gold_items.csv` separately for item-level
precision. The sample excludes duplicate and low-effort posts, so it does not measure those filters.
It contains full review text: keep it private. Re-exporting to the same path overwrites the CSV and
its labels; use `--out` to preserve earlier sets. `tests/fixtures/rules_gold_english.csv` is a small
synthetic regression set, not an estimate of real-world quality.

### Re-running after rule changes

```powershell
docker compose --profile pipeline build app
docker compose run --rm app extract --redo       # deletes all wants and re-extracts from posts
docker compose run --rm app catalog
docker compose run --rm app context --refresh    # recompute scope + genres for every row
```

`--redo` deletes `want_item` rows and extraction-attempt records; posts, raw files, and the catalog
are untouched. Cached Azure responses remain available for re-extraction.

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
flags, non-game software) are filtered out when resolving. Public store-page user-defined tags are
available in the local explorer but are not yet used for context resolution or clustering.
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
