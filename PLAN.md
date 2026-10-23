# Whitespace: Plan & Checklist

Mine player demand ("I wish it had X") from public sources, tie each want to the **game and genre
it was said about**, cluster wants into niches, and score each niche by demand vs. supply.

Legend: `[x]` built, `[ ]` not built. Nothing here has been run by the author in Docker; the test
commands at the bottom are for you to run.

---

## 0. Decisions made so far

- Name: **Whitespace** (package `whitespace`, CLI `whitespace`)
- **No LLM anywhere.** Extraction is rule-based: regex prefilter, spaCy dependency parse, gazetteer
  NER, VADER + lexicons for intensity
- Everything runs in **Docker**; tests + lint + CI included
- Scraping: **Scrapling** for HTML sources, official APIs wherever they exist
- Storage: SQLite (`data/whitespace.sqlite`) + untouched raw files (`data/raw/`)

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
- [ ] Expose `max_pages` in config + one-time **backfill mode** (today: 20 pages x 100 per game,
      then only newer reviews on later runs)
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

---

## 3. Cleaning

- [x] Dedupe (exact hash + near-duplicate shingles), language detection, low-effort filter
- [x] Review-bomb detection function (robust median/MAD)
- [ ] **Wire review-bomb flags into the `clean` command** (function exists, not called yet)
- [ ] Review the low-effort threshold: 55% of the first Steam batch was flagged (`min_words=8`)
- [ ] Bot/spam heuristics (account age, posting rate) where the source exposes them

---

## 4. Extraction (rule-based)

- [x] Regex prefilter with trigger offsets
- [x] spaCy dependency parse + `with` / `without` / `no` marker rules, filler stripping
- [x] Gazetteer NER (EntityRuler): game names + aliases -> Steam appids
- [x] Intensity: strong phrases / VADER magnitude -> 3, moderate verbs -> 2, neutral -> 1
- [x] Strict schema validation, `referenced_games` + `referenced_appids` stored

### Quality fixes: second pass (written, awaiting your test run)

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
- [x] Third pass, from your first `rules-v1` run (35 wants from 45 trigger posts out of 1806 cleaned):
      Steam BBCode (`[h1]`, `[url=...]`) stripped before parsing; `games_like` with **no named game**
      ("many games like this") is dropped, or becomes a `genre_request` if it carries a feature;
      pure "like X" wants get subject `genre`
- [x] Gold sampler for **non-trigger posts** (`gold-export --kind untriggered`): measures how many real
      wishes the prefilter never sees. First `rules-v1` run: 45 trigger posts in 1806 cleaned, 17 of
      them produced no want
- [ ] **You:** label the three CSVs (or paste them and I'll review), then widen the triggers from
      evidence, not guesses
- [x] Fourth pass, from the first gold review (written, awaiting your test run): target search stops at
      the clause boundary ("..., but ..."); "some kind of X" resolves to X; `less X` keeps its
      polarity; "I wish this wasn't X" becomes a `complaint` ("without X"); every game in a
      "games like A or B" list is captured; off-topic targets (steam, sequel, series, genre, pc, ...)
      are junk; `checkpoint`, `replayability`, `rebind`, `instructions` get subjects; curly
      apostrophes no longer defeat the trigger regexes; **extraction now skips non-English posts**
      (`non_english` in the extract stats)
- [ ] Known leftovers: negative quantifiers read as wants ("death with little content" should be a
      complaint); one-word items like "upgrade" / "quest" are vague; "modes difficulty setting"
      style phrases are noisy
- [ ] **You:** label ~300 items (`correct` = y/n) and ~100 missed posts (`should_extract` = y/n);
      record precision and miss rate here, then tune `rules.py` against them
- [ ] Relative-clause wishes with no marker ("a game where you run a bakery") are still missed
- [ ] Unify lowercase / abbreviation resolution between the gazetteer and the `like <X>` fallback

### Findings from the first gold review (my reading of your pasted samples, not your labels)

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

### Decisions made

- [x] **English only** for the rules extractor. Gemini now reads **all languages** by default (the first
      sample was 51% non-English); `--languages english` restricts it
- [x] **Separate complaint channel**: `request_type = complaint`, written as the missing thing in positive
      form. Built for the rules extractor; the Gemini prompt asks for the same convention
- [x] **LLM extraction via the free Gemini API** (after trying to plan around Groq, whose free tier now
      lists only gpt-oss and Qwen models at 8K tokens/min). The rules extractor stays as the offline
      fallback and the comparison baseline

### Gemini extraction: built (written, awaiting your test run)

- [x] `GeminiExtractor`: JSON-schema output, verbatim-quote check, strict item validation, appid
      resolution through the games index, per-minute 429 wait, daily-quota clean stop, 5xx retry
- [x] `llm_cache` table: re-runs and `--redo` are free
- [x] `extract --extractor gemini|rules`, `--prefilter cue|all`, `--model`, `--rpm`, `gemini-models`
- [x] `extractor_version` in the gold CSV and `by_extractor` in `gold-score`
- [x] First 100-post Gemini run (99 wants, all with genres): good concrete wants ("shared sync quests in
      co-op", "left handed support", "flexible soil placement"), but ~76% were `complaint`, many vague
      ("better fishing", "better art style"), and confidence sat at 0.8-1.0 so it could not filter.
      Fixed in prompt `gemini-v3`: new `dislike` request type, ban on "better X" summaries,
      actionability-based confidence, and rules against reporting what the player did themselves
- [x] Full v1 run: 848 posts, 954 items, 103 requests, 0 retries, 13 quote-check drops (1.4%). Batching
      and the quote guard behave
- [x] Found two leaks: 900 of 1,748 posts were skipped as non-English, and the low-effort filter dropped
      CJK posts because it counted spaces. Both fixed (prompt `gemini-v3`; `clean --refresh`)
- [ ] **You:** re-run 100 posts on `gemini-v3` and compare: `redo LIMIT=100`, expect fewer, sharper wants
      and a spread of confidence values
- [ ] **You:** `gemini-models`, then `extract --limit 100`, then label ~100 of the new items and compare
      precision with `rules-v2` via `gold-score`
- [ ] Pass the game being reviewed (and its genres) into the prompt: helps "this game" and
      "like X" cases
- [x] **Batching** (8 posts per request, per-post cache, character budget, single-post fallback when a
      batch response is unparseable). ~8x fewer requests on a small free quota
- [ ] If the daily quota still bites: the paid tier is a few cents for ~2k posts and stops Google using the
      content
- [x] Default model is `gemini-3.5-flash-lite` (`gemini-2.5-*` stopped working on a free key)
- [ ] Check AI Studio for the free quotas of your model and set `GEMINI_RPM` to match

---

## 5. Context: tie every want to a game and genre

**Problem:** "faster progression system" means nothing alone. It matters when it's said about a
specific game in a specific genre. Each want now carries that context.

**Two different games per want, kept separate:**
- **Context game** (`context_appid`): the game being discussed (a Steam review's own app)
- **Referenced games** (`referenced_appids`): games mentioned for comparison ("games like Hades")

### Built (written, not yet run by the author)
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
- [ ] Finer-grained **user tags** (e.g. "Farming Sim", "Roguelite") from a tag source; verify the
      source's terms and rate limits first
- [ ] Use categories (`catalog_game.tags`) as extra context, e.g. already-has-Co-op games
- [ ] Forum board -> genre: `ForumCrawler` records only the forum name, not the board
- [ ] Steam discussion/hub threads as a context-rich source
- [ ] Dashboard filters by genre and game; quotes show which game they were about
- [ ] Niche key = cluster + dominant context genres (needs the `cluster` command)
- [ ] Dissatisfaction rate and supply join restricted to games sharing the niche's genres

---

## 6. Clustering

- [x] `cluster_wants()` (embeddings + UMAP + HDBSCAN, `ml` extra) and cluster-drift matching
- [x] `cluster/text.py`: context-prefixed text to embed
- [ ] `cluster` CLI command: read `want_item`, cluster with context, write `cluster` / `cluster_member`
- [ ] Name clusters from **c-TF-IDF keywords** (BERTopic does this, no LLM)
- [ ] Re-cluster on a schedule and track drift (splits/merges) using `drift.py`
- [ ] Flag clusters dominated by one source or one thread (>60%)
- [ ] Optional: replace MiniLM embeddings with TF-IDF if you want zero neural models

---

## 7. Supply-gap scoring

- [x] Gap formula (versioned): demand (mentions, growth, intensity, communities) x dissatisfaction
      / supply pressure (review score, review count confidence, recency)
- [ ] Build `catalog_game` (genres, tags, release date, price, reviews, followers)
- [ ] Per-niche stats: weighted mentions, growth vs. previous window, distinct communities
- [ ] Dissatisfaction rate from negative reviews of matched games
- [ ] `score` CLI command writing `niche_score`
- [ ] Backtest: did known successful games score as gaps before release?

---

## 8. Serving

- [x] Streamlit app reading `niche_score` (shows an empty state until scoring exists)
- [ ] Niche explorer: trend lines, representative quotes (with game), closest existing games
- [ ] Weekly report (Markdown/email)
- [ ] Persist the SQLite file between scheduled CI runs, then enable the cron in `pipeline.yml`
- [ ] Hosting: Streamlit Community Cloud or a static export (keep raw data private)

---

## 9. Milestones

| # | Goal | Done when |
|---|---|---|
| M1 | Ingest + clean + extract running in Docker | **Reached** (Steam, 2 games, extract runs) |
| M2 | Extraction quality | Junk fixes written (**awaiting your run**); gold set labeled; precision >= target (e.g. 80%) |
| M3 | Context | Every `want_item` has context game, genres, scope. **Code written, awaiting your test run** |
| M4 | Niches | `cluster` command; named clusters look coherent on manual review |
| M5 | Gap scoring | Ranked niche list with quotes, games, and closest supply |
| M6 | More sources + hardening | 5+ sources, drift tracking, scheduled runs with persisted data |

---

## 10. Test commands (run these yourself; PowerShell)

```powershell
# format first (auto-fixes style), then lint + tests
docker compose run --rm fmt
docker compose run --rm lint
docker compose run --rm test

# rebuild the pipeline image after pulling new code (run does NOT rebuild)
docker compose --profile pipeline build app

# re-run extraction with the new rules, then context
docker compose run --rm app extract --redo      # deletes all wants, re-extracts from posts
docker compose run --rm app catalog
docker compose run --rm app context --refresh   # recompute scope + genres (applies the stoplist)
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

---

## 11. Risks & open questions

- **Rule precision:** the real-data sample had visible junk. Expect to iterate against the gold set
  rather than trust the output
- **ToS drift:** Reddit and others change access terms; re-review quarterly
- **Bias:** vocal minorities and review-bombing skew demand; weight by distinct authors
- **Genre tags are noisy:** user tags vary wildly; use the top N by weight and a small allowlist
- **Demand is not market size:** sanity-check any niche before acting on it
