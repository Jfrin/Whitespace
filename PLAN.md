# Whitespace: Plan & Checklist

Mine player demand ("I wish it had X") from public sources, tie each want to the **game and genre
it was said about**, cluster wants into niches, and score each niche by demand vs. supply.

Legend: `[x]` implemented, `[ ]` outstanding. Implementation does not imply that a feature has
been validated on current production data. Historical experiment notes below are not current
quality measurements.

**Current status:** Steam ingest, cleaning, extraction, catalog, and context are wired into the
CLI and local dashboard. Azure Foundry is the default extractor; English-only rules are the offline
alternative. Clustering and scoring have building blocks but are not wired into the pipeline.

### Make the data decision-useful

Prioritize trustworthy evidence over more sources or a polished score:

1. [ ] **Measure extraction quality.** Label representative real reviews and extracted wants,
       including reviews with no extracted result; report precision and recall, sample sizes, and
       breakdowns by language and request type before scaling.
2. [ ] **Show coverage and bias.** Report reviews collected, filtered, and processed by game,
       source, time window, and extractor version. Steam currently defaults to negative reviews;
       do not present this sample as all players or infer population demand from it.
3. [ ] **Build an evidence-backed niche explorer.** Group similar requests while distinguishing
       game-specific changes from requests for a different kind of game. Show distinct review
       counts, representative and contradictory quotes, and the underlying game context.
4. [ ] **Validate supply matches.** Show comparable games and why they match the requested
       features; genre or user-tag overlap alone does not prove a feature is present. Validate
       matches before producing ranked gap scores.
5. [ ] **Make claims inspectable.** Each opportunity card should show its time window, processed
       review denominator, games, source quotes and links, coverage, and limitations. Investigate
       stable individual-review links where permitted; current Steam links point to the game's
       review listing. Clearly label missing or stale evidence.

Helpful/funny votes and user-defined tags are context, not market size or independent demand;
do not automatically use them to boost scores without bias and quality evaluation.

### Provisional AI-assisted quality check

- A seeded local sample of 100 reviews and 100 extracted items was drawn independently from the
  currently eligible `azure-v3:gpt-4.1-mini` cohort with completed, non-content-filtered cached
  responses. Review labels were assigned without displaying the model's predictions. Files and
  sampling details are in `data/evaluation_2026-11-02/`; they contain review text and are private.
- The AI reviewer marked 52 items usable, 42 incorrect/out of scope, and 6 uncertain. Among the
  94 decisive labels, 52/94 (55.3%) were usable. This applies the stated usefulness rubric and is
  not independent human-validated precision.
- For post-level detection, decisive AI labels gave 64 true positives, 20 false positives, 11 true
  negatives, and 0 false negatives; 5 were uncertain. The resulting 64/64 recall checks whether
  *any* item was emitted, not whether its content was correct or complete; it is not evidence of
  perfect extraction. The cohort excludes unprocessed, low-effort, duplicate and content-filtered
  reviews, and the review text was limited to the first 3,000 normalized characters.
- [ ] Independently review the sample with human labels and document disagreements. Investigate
      technical bugs leaking into output, unsupported summaries, and vague sentiment being framed
      as actionable requests; evaluate excluded and unprocessed reviews separately.

### Earlier reported run

- Docker formatting and lint passed; tests reported **275 passed, 1 skipped**, with **93.12%**
  coverage. The pipeline image built successfully.
- Azure processed 100 candidate posts and produced 118 items; 21 item quotes failed the verbatim
  check, two posts were content-filtered, and 21 responses were cache hits. The run used one post
  per request. These counts describe the run, not extraction precision or recall.
- After context, the database had 6,247 posts and 280 wants; all 280 wants had genres. No clusters
  or niche scores existed. The context command processed 157 rows, all with genres resolved.
- Gold exports contain 280 items and 100 no-want trigger posts; both are unlabeled, so precision
  and the reported missed rate are not yet available. Label the CSVs before scoring again.
- The `missed` sample includes qualifying posts without wants, including potentially unprocessed or
  filtered posts; its score is not an overall extraction recall estimate.

---

## 0. Decisions made so far

- Name: **Whitespace** (package `whitespace`, CLI `whitespace`)
- Extraction defaults to **Azure Foundry** (`gpt-4.1-mini` deployment by default); English-only
  spaCy rules remain an offline fallback and comparison baseline. Offline rules skip posts tagged
  as non-English without deleting them; missing language tags are treated as English
- Everything runs in **Docker**; tests + lint + CI included
- Scraping: **Scrapling** for HTML sources, official APIs wherever they exist
- Storage: SQLite (`data/whitespace.sqlite`) + untouched raw files (`data/raw/`)
- The Streamlit dashboard is a local, unauthenticated pipeline console, not a hosted service

---

## 1. Groundwork

- [x] Repo layout, `pyproject.toml`, ruff + pytest config, coverage gate (75%)
- [x] Dockerfile (targets `dev`, `prod`, `dashboard`), `docker-compose.yml`, Makefile
- [x] CI: build dev image, ruff, format check, pytest; build prod + dashboard images, CLI smoke test
- [x] Source registry (`config/sources.yaml`) with `tos_reviewed` field
- [x] SQLite schema + run ledger + **automatic migration for older databases**
- [ ] Fill `tos_reviewed` for every enabled source after reading its ToS / robots.txt
- [ ] Decide commercial vs. internal-research use (changes what several sources allow)

---

## 2. Ingestion

### Framework
- [x] `SourceCrawler` base: rate limiter, incremental cursors, untouched raw storage, run ledger
- [x] Re-running never duplicates raw files or posts
- [x] Failed runs are ledgered and do not advance the cursor
- [x] Support `max_pages` in config or CLI and resumable backfills for Steam and Reddit;
      source retention and cursor behavior still limit available history
- [ ] Canary tests per crawler that hit the real site (marked `network`, run manually)

### Sources

| Source | Method | Status |
|---|---|---|
| Steam reviews | Official endpoint | [x] built, running |
| Reddit | Official OAuth API | [x] built, disabled until credentials + terms review |
| Generic HTML forum | Scrapling `Fetcher` + CSS selectors | [x] built, config template only |
| Steam discussions | Scrapling | [ ] |
| ResetEra / RPG.net / Paradox / Total War forums | Scrapling, one selector set each | [ ] |
| Google Play / App Store / TapTap | Scrapling or public feeds (check terms) | [ ] |
| itch.io | Scrapling | [ ] |
| Kickstarter | Scrapling `DynamicFetcher` | [ ] |
| Steam wishlist/follower signals | Store page data | [ ] |
| YouTube comments | YouTube Data API | [ ] |
| Discord / anything behind a login | **Excluded on purpose** | n/a |

- [x] Reddit: **subreddit -> genre map** in `config/context.yaml`
- [ ] Forums: **board -> genre/game map** (today only the forum name is recorded)

### Steam review engagement

Steam's helpful votes are different from a review's positive/negative recommendation. The official
reviews API exposes helpful and funny votes and a separate weighted helpfulness score.

- [x] Capture available helpful/funny votes, recommendation, weighted helpfulness, comments and
      playtime; preserve missing values separately from zero
- [x] Recover metrics from stored raw responses and refresh existing reviews using independent,
      rate-limited, resumable cursors; store the latest observation time without changing ingest cursors
- [x] Show available review metrics and support helpful/funny sorting in the local explorer
- [ ] Retain historical metric snapshots if trend analysis needs them; currently only the latest
      observation is stored, and Steam's anonymous API responses can be cached
- [ ] Evaluate capped or normalized helpfulness weighting for representative quotes and niche
      scoring; test for age, popularity, manipulation, and review-bomb bias before enabling it
- [ ] Review source terms and validate metrics on representative real reviews before scoring

---

## 3. Cleaning

- [x] Dedupe (exact hash + near-duplicate shingles), language detection, low-effort filter
- [x] Review-bomb detection function (robust median/MAD)
- [ ] **Wire review-bomb flags into the `clean` command** (function exists, not called yet)
- [ ] Review the low-effort threshold: 55% of the first Steam batch was flagged (`min_words=8`)
- [ ] Bot/spam heuristics (account age, posting rate) where the source exposes them

---

## 4. Extraction

### Current LLM extraction

- [x] Azure Foundry is the default; offline rules are available through the CLI
- [x] Multilingual extraction, batched requests, per-post cache, retries and resumable runs
- [x] Balance limited extraction across ingested games and track completed posts with no items;
      previously ingested posts remain available for later runs without repeating empty results
- [x] Schema validation and verbatim source-quote checks; record extractor version per item
- [x] Azure content-filter handling: retry blocked batches individually and cache blocked posts
- [x] Separate `dislike` items from actionable wants and complaints
- [ ] Label current Azure output and measure precision and misses against the gold set; compare
      against rules before relying on results
- [ ] Pass the reviewed game and its genres into the extraction prompt
- [ ] Review provider data handling, pricing and deployment quotas before processing public text

### Offline rules baseline

- [x] Regex prefilter with trigger offsets
- [x] spaCy dependency parse + `with` / `without` / `no` marker rules, filler stripping
- [x] Gazetteer NER (EntityRuler): game names + aliases -> Steam appids
- [x] Intensity: strong phrases / VADER magnitude -> 3, moderate verbs -> 2, neutral -> 1
- [x] Strict schema validation, `referenced_games` + `referenced_appids` stored
- [x] `rules-v3`: separate independent wishes and complaints, preserve more/less grind direction,
      and reject some negated-absence false positives
- [x] English post-level gold sampling and scoring (`gold-export` / `gold-score --kind rules`),
      including the cue prefilter and posts irrespective of prior extraction
- [x] Small labeled synthetic English regression set; it is not a real-world quality estimate
- [ ] Label a representative real English post sample and measure post-level precision and recall;
      separately label extracted items to assess item-level precision and partial misses
- [x] `rules-v4`: improve selected negation, hypothetical-clause, and vague non-design cases
- [ ] Validate remaining negation, hypothetical and vague-feedback cases on human-labeled real reviews
- [x] Preserve exact original source quotes even when the rules extractor normalizes punctuation,
      whitespace, or markup
- [x] Process long reviews using bounded parse windows; extra-long individual sentences can still
      lose context at window boundaries
- [x] Compare Azure and offline rules on shared posts without deleting existing wants; track
      completed, filtered and legacy-unknown attempts per version, including empty results
- [ ] Independently label a representative sample before interpreting comparison results as quality
      measurements. A preliminary AI-labeled comparison of 29 English reviews is saved in
      `data/evaluation_2026-11-02/rules_v4_with_gazetteer_comparison.json`; it is not human ground truth.

### Rules quality history

From the first real run (25 random rows: "like this", "hour without reason", "meh rating"):

- [x] `games_like` objects that are pronouns/demonstratives/generic nouns are dropped, and `like` must
      be a comparison ("games like X", "something like X"), not "it feels like this"
- [x] Non-design heads: stoplist widened (hour, reason, fix, rating, ...); `with`/`without`/`no`
      features whose head is junk are skipped
- [x] Subject vocabulary widened: new `content` (story, quests, levels) and `ux` (UI, camera,
      tutorial, controls) subjects; progression/skills/loot map to `mechanic`
- [x] Items with no design subject get **confidence -0.25** (flagged, not deleted); clustering inputs
      keep `confidence >= 0.6` by default
- [x] Aliases for partial names ("DBD", "Binding", "FTL", "Gungeon", ...) in `config/games.yaml`.
      Verify those appids before relying on them
- [x] Extractor version `rules-v1`; **`extract --redo`** deletes all wants and re-runs the new rules
- [x] **Gold-set tooling:** `gold-export` samples rows to label, `gold-score` reports precision overall,
      by request type / subject / scope, and above vs. below the confidence threshold
- [x] Third pass, from the first `rules-v1` run (35 wants from 45 trigger posts out of 1806 cleaned):
      Steam BBCode (`[h1]`, `[url=...]`) stripped before parsing; `games_like` with **no named game**
      ("many games like this") is dropped, or becomes a `genre_request` if it carries a feature;
      pure "like X" wants get subject `genre`
- [x] Gold sampler for **non-trigger posts** (`gold-export --kind untriggered`): measures how many real
      wishes the prefilter never sees. First `rules-v1` run: 45 trigger posts in 1806 cleaned, 17 of
      them produced no want
- [ ] Label the three CSVs, then widen the triggers from evidence, not guesses
- [x] Fourth pass, from the first gold review: target search stops at
      the clause boundary ("..., but ..."); "some kind of X" resolves to X; `less X` keeps its
      polarity; "I wish this wasn't X" becomes a `complaint` ("without X"); every game in a
      "games like A or B" list is captured; off-topic targets (steam, sequel, series, genre, pc, ...)
      are junk; `checkpoint`, `replayability`, `rebind`, `instructions` get subjects; curly
      apostrophes no longer defeat the trigger regexes; **extraction now skips non-English posts**
      (`non_english` in the extract stats)
- [ ] Known leftovers: negative quantifiers read as wants ("death with little content" should be a
      complaint); one-word items like "upgrade" / "quest" are vague; "modes difficulty setting"
      style phrases are noisy
- [ ] Label ~300 items (`correct` = y/n) and ~100 missed posts (`should_extract` = y/n);
      record precision and miss rate here, then tune `rules.py` against them
- [ ] Relative-clause wishes with no marker ("a game where you run a bakery") are still missed
- [ ] Unify lowercase / abbreviation resolution between the gazetteer and the `like <X>` fallback

### Historical findings from the first gold review (informal review, not labels)

- **Items (31):** ~7 clean design wishes, ~8 accurate "like X" references (comparisons, not requests),
  ~16 junk. Junk is concentrated below the 0.6 confidence line (~1 in 10 correct there vs ~2 in 3
  above), so the threshold earns its keep. Junk causes: wishes about Steam itself, polarity ("wish
  this wasn't X"), a later clause hijacking the target, and the wish being about the player
  ("wish I listened to my gut")
- **Missed trigger posts (17):** only ~1 was a true trigger miss ("i wish there was some kind of
  checkpoint ..."); most had no real wish in them
- **Untriggered posts (100):** explicit requests are rare (~3%). The demand is mostly **implicit**:
  "no fast restarts", "no instructions", "can't use axe on Shane", "unplayable without the wiki",
  "no way to rebind controls". About 15-20% of English posts carry some design feedback of this kind
- **Language:** roughly a third of the sample is not English (Russian, Turkish, Portuguese, Korean,
  Spanish, French, Japanese, Polish). The rules are English-only, so that demand is invisible

**Implication:** widening trigger phrases would not have helped; the sample says the gap is
*complaints about missing features*, and non-English text. Both are product decisions, listed below.

### Earlier LLM experiment (historical)

- An earlier Gemini integration was removed; historical data and extractor-version labels remain.
- Its first 100-post run produced 99 wants, many vague, with confidence concentrated at 0.8-1.0.
  This led to a separate `dislike` type and actionability-based confidence in the shared prompt.
- A later run processed 848 posts, producing 954 items with 13 quote-check drops. These historical
  observations do not establish current Azure quality.
- Language handling and CJK low-effort filtering were corrected after earlier non-English posts were
  skipped. The rules extractor remains English-only; Azure supports all languages by default.

---

## 5. Context: tie every want to a game and genre

**Problem:** "faster progression system" means nothing alone. It matters when it's said about a
specific game in a specific genre. Each want now carries that context.

**Two different games per want, kept separate:**
- **Context game** (`context_appid`): the game being discussed (a Steam review's own app)
- **Referenced games** (`referenced_appids`): games mentioned for comparison ("games like Hades")

### Built
- [x] `want_item` columns: `context_appid`, `context_genres` (JSON), `scope`, `source_context`
      (auto-migrated on open, so existing databases keep working)
- [x] `catalog_game` gets `genres` + `fetched_at`; `whitespace catalog` fetches genres and store
      categories (Co-op, Multi-player) from Steam's public store `appdetails` endpoint: cached for 30
      days, rate limited, stops on HTTP 429 and resumes next run, misses are remembered
- [x] Scope rules (`rules.classify_scope`): `reference` ("games like Y but..."), `genre_wide`
      ("why doesn't anyone make...", "is there a game..."), `game_specific` (said about the game the
      post is attached to)
- [x] `whitespace context` pass: sets scope + context game + genres on every want. Genre priority:
      for `reference` wants the *referenced* games' genres first, otherwise the context game's, then
      the subreddit/forum map in `config/context.yaml`. Unresolved rows stay NULL and are retried
- [x] `config/context.yaml`: subreddit / forum -> genre map
- [x] `cluster/text.py`: builds `"action, rpg: faster progression system"` for embedding

### Still to do
- [x] **Genre stoplist:** Indie, Early Access, Free To Play, Gore, Nudity, and non-game software
      categories are filtered out when resolving genres; `context --refresh` re-applies it
- [ ] Use categories (`catalog_game.tags`) as extra context, e.g. already-has-Co-op games
- [ ] Forum board -> genre: `ForumCrawler` records only the forum name, not the board
- [ ] Steam discussion/hub threads as a context-rich source
- [x] Local dashboard filters by genre and game; quotes show which game they were about
- [ ] Niche key = cluster + dominant context genres (needs the `cluster` command)
- [ ] Dissatisfaction rate and supply join restricted to games sharing the niche's genres

### Steam user-defined tags

Steam store pages display user-defined tags such as "Farming Sim" and "Roguelite". These can add
more useful game-type context than broad genres, but are noisy user labels, not proven features or
measures of demand. The current `catalog_game.tags` field contains Steam **store categories**, not
these user-defined tags; keep them separate.

- [ ] Review Steam's applicable terms, robots.txt, and permitted collection and display before use
- [x] Fetch public store-page tags explicitly, without bypassing age gates, login, or restrictions
- [x] Add a separate user-tag table with source, fetch timestamp, and status;
      preserve existing genres and categories
- [x] Fetch and cache tags per appid with conservative rate limits and preserve prior data when a
      page is unavailable, restricted, or malformed; retry failures on a later run
- [x] Normalize and deduplicate tags without treating display order as an exact vote count
- [ ] Identify a configurable set of niche-relevant tags
- [ ] Attach or resolve appropriate tags for the context game versus referenced games; keep the
      existing genre fallback when tags are unavailable
- [x] Add tag-based filtering to the local explorer
- [ ] Add tag-based filtering to the public explorer after a real dataset and publication review
- [x] Test parsing, caching, restrictions and failures offline
- [ ] Manually review sample tags and test context fallbacks before using tags in clustering

---

## 6. Clustering

- [x] `cluster_wants()` (embeddings + UMAP + HDBSCAN, `ml` extra) and cluster-drift matching
- [x] `cluster/text.py`: context-prefixed text to embed
- [ ] `cluster` CLI command: read `want_item`, cluster with context, write `cluster` / `cluster_member`
- [ ] Experiment with selected user tags as additional context; compare cluster coherence against
      genre-only inputs before making tags the default
- [ ] Name clusters from **c-TF-IDF keywords** (BERTopic does this, no LLM)
- [ ] Re-cluster on a schedule and track drift (splits/merges) using `drift.py`
- [ ] Flag clusters dominated by one source or one thread (>60%)
- [ ] Optional: replace MiniLM embeddings with TF-IDF if you want zero neural models

---

## 7. Supply-gap scoring

- [x] Gap formula (versioned): demand (mentions, growth, intensity, communities) x dissatisfaction
      / supply pressure (review score, review count confidence, recency)
- [x] Basic `catalog_game` with Steam genres and store categories for context
- [ ] Extend the catalog with supply signals (release date, price, reviews, followers)
- [ ] Per-niche stats: weighted mentions, growth vs. previous window, distinct communities
- [ ] Dissatisfaction rate from negative reviews of matched games
- [ ] `score` CLI command writing `niche_score`
- [ ] Backtest: did known successful games score as gaps before release?

---

## 8. Serving

- [x] Local Streamlit console: configure and run the pipeline or individual stages, monitor progress,
      and inspect recent wants and quotes
- [x] Local coverage table shows ingested posts vs. extracted items for every game, including
      configured IDs with no posts and games with no extracted wants
- [x] Read and display `niche_score` when available (empty state until scoring is wired up)
- [ ] Niche explorer: trend lines, representative quotes (with game), closest existing games
- [ ] Weekly report (Markdown/email)
- [ ] Persist the SQLite file between scheduled CI runs, then enable the cron in `pipeline.yml`
- [ ] Hosting: Streamlit Community Cloud or a static export (keep raw data private)

### Local data explorer

**Goal:** make it easy to sift through the real extracted feedback, rather than only the most recent
100 rows. Keep pipeline controls in the local console; the public demo must stay read-only.

- [x] Replace the fixed 100-row results view with searchable, paginated results and clear total vs.
      filtered counts; filter across the full dataset before pagination
- [x] Add combinable context-game, genre/game-type, subject, request-type, and confidence filters
- [ ] Add filters for Steam user-defined tag (when available), store category, scope, language,
      source, and extractor
- [ ] Add confidence and intensity filters, plus sorting and an option to reset all filters;
      explain that confidence is a heuristic, not a probability
- [ ] Show each result's summary, subject, request type, game, genres, user tags, confidence,
      extractor version, original quote, and source link where available; distinguish the game
      discussed from games mentioned for comparison
- [ ] Clearly handle missing games, genres, tags, quotes, or source links; do not fabricate values
- [ ] Add useful summaries of the filtered data (counts by game, genre, tag, subject and request
      type), with labels that distinguish extracted items from unique posts and real-world demand
- [ ] Make the explorer usable on desktop and mobile, with clear empty, loading, and error states
- [x] Test combined existing filters, pagination, missing metadata, and counts against fixture data
- [ ] Test additional filters and sorting, including games with multiple user-defined tags

### Public Azure demo: read-only first

**Goal:** a resume-ready public site where visitors explore reviewed player feedback without access
to pipeline controls, credentials, or paid API calls. This is separate from the local pipeline console.

**Architecture:** reviewed demo data -> read-only Streamlit dashboard -> Azure Container Apps.
Package the reviewed dataset with the release; refresh it locally and redeploy when needed. Live
visitor-submitted analysis is outside the first release.

#### 1. Prepare the demo data

- [ ] Choose the games to include; the current Steam configuration uses Stardew Valley and Hades
- [ ] Label and review a representative sample of current extracted wants; remove obvious junk
- [ ] Export a separate public dataset with only necessary fields: summaries, subjects, request
      types, game/genre context, reviewed user tags, short quotes, and source links where appropriate
- [ ] Exclude raw reviews, author identifiers, LLM caches, credentials, and private operational data
- [ ] Review Steam's terms and quote usage before publishing
- [ ] Record the dataset date, scope, and limitations; distinguish curated demo counts from totals

#### 2. Build the public dashboard

- [x] Separate read-only local demo app with no pipeline controls or credential fields
- [x] Overview and filters for game, genre, request type, and confidence
- [x] Show fictional examples with quotes and context, explain limitations, and link to the repository
- [x] Clearly identify clustering and gap scoring as not yet available
- [x] Read a packaged JSON dataset without opening the production database
- [ ] Bring subject and reviewed user-tag filters and the improved browsing experience to the public
      explorer; only expose fields explicitly approved for publication
- [ ] Add reviewed real data and source links where appropriate, after checking terms

#### 3. Deploy to Azure

- [x] Build a dedicated dashboard container with only the public app and synthetic dataset
- [ ] Replace synthetic examples with a reviewed public dataset before deploying
- [ ] Deploy to Azure Container Apps with public HTTPS ingress
- [ ] Keep API keys, pipeline controls, and writable production data out of the public container
- [ ] Configure logs, health checks, and an Azure budget with alerts; estimate costs before deployment
- [ ] Start with a single replica and verify Streamlit sessions work reliably
- [ ] Document how to regenerate the reviewed dataset and redeploy it

#### 4. Validate before sharing

- [ ] Run tests and check the dashboard locally with the public dataset
- [ ] Verify that no public control or route can start a pipeline job
- [ ] Inspect the container image and public responses for secrets, raw data, and identifiers
- [ ] Test the deployed site on desktop and mobile, including filters and source links
- [ ] Check startup behavior, logs, and actual Azure costs

#### 5. Resume polish

- [ ] Add screenshots, a live-demo link, and architecture notes to `README.md`
- [ ] Describe it accurately as a player-feedback extraction and exploration tool; gap scoring is
      planned, not yet integrated

### Suggested order

1. Build the local explorer using existing games, genres, subjects, and request types.
2. Review Steam's terms, then add user-tag collection and tag filters if permitted.
3. Curate a public dataset and update the read-only demo; verify its privacy and source links.
4. Deploy the demo to Azure; add clustering and scoring later, after validating their results.

---

## 9. Milestones

| # | Goal | Done when |
|---|---|---|
| M1 | Ingest + clean + extract in Docker | **Implemented** for Steam; dashboard can run the full current pipeline |
| M2 | Extraction quality | Label representative samples; agree on precision and miss-rate targets and meet them |
| M3 | Context | **Implemented**; validate coverage and accuracy, including unresolved games and genres |
| M4 | Niches | `cluster` command; named clusters look coherent on manual review |
| M5 | Gap scoring | Ranked niche list with quotes, games, and closest supply |
| M6 | More sources + hardening | 5+ sources, drift tracking, scheduled runs with persisted data |

---

## 10. Test and evaluation commands (PowerShell)

```powershell
# format first (auto-fixes style), then lint + tests
docker compose run --rm fmt
docker compose run --rm lint
docker compose run --rm test

# rebuild the pipeline image after pulling new code (run does NOT rebuild)
docker compose --profile pipeline build app

# run the default Azure extractor, then context
docker compose run --rm app extract --limit 100
docker compose run --rm app catalog
docker compose run --rm app context
docker compose run --rm app stats

# eyeball the result
docker compose run --rm --entrypoint python app -c "import sqlite3; c=sqlite3.connect('/app/data/whitespace.sqlite'); [print(r) for r in c.execute('select request_type, subject, want_summary, round(confidence,2) from want_item order by random() limit 30')]"

# how much junk is left? low-confidence items should be the junkiest
docker compose run --rm --entrypoint python app -c "import sqlite3; c=sqlite3.connect('/app/data/whitespace.sqlite'); [print(r) for r in c.execute('select subject, count(*), round(avg(confidence),2) from want_item group by 1 order by 2 desc')]"

# build the gold set: label the CSV in Excel/Sheets (y or n), save, then score
docker compose run --rm app gold-export --kind items --n 300      # -> data/gold_items.csv
docker compose run --rm app gold-export --kind missed --n 100     # -> data/gold_missed.csv
docker compose run --rm app gold-score --kind items
docker compose run --rm app gold-score --kind missed
```

`gold_items.csv` opens in Excel with correct accents. Fill the `correct` column (items) or
`should_extract` column (missed) with `y` or `n`. Unlabeled rows are ignored.

To compare the offline baseline, use `extract --extractor rules`. `extract --redo` deletes **all**
existing wants before re-extraction, even when combined with `--limit`; only use it deliberately.
Azure requests may incur charges. Rebuild the relevant images before testing code changes.

---

## 11. Risks & open questions

- **Extraction quality:** historical samples contained junk; measure current Azure and rule
  outputs against labeled examples rather than assuming precision or recall
- **Provider risk:** LLM extraction sends public text to the configured provider and may incur costs;
  check data handling, quotas, filtering and pricing
- **ToS drift:** Reddit and others change access terms; re-review quarterly
- **Bias:** vocal minorities and review-bombing skew demand; weight by distinct authors
- **Genre tags are noisy:** user tags vary wildly; use the top N by weight and a small allowlist
- **Demand is not market size:** sanity-check any niche before acting on it
