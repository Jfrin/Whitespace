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

No API keys are needed anywhere. Extraction is rule-based (spaCy), not an LLM. Data lands in
`./data` (gitignored).

## Layout

| Path | What |
|---|---|
| `config/sources.yaml` | Source registry: enabled flag, rate limit, ToS URL, `tos_reviewed` date |
| `ingest/` | `base.py` (rate limit, cursors, raw store, run ledger), `steam_reviews.py` (official API), `reddit.py` (official OAuth API), `forum.py` (Scrapling HTML) |
| `clean/` | Language, low-effort, near-dup, review-bomb detection. Flags, never deletes |
| `extract/` | `prefilter.py` (regex candidates), `nlp.py` (spaCy rules), `rules.py` (pure-Python lexicons), `gazetteer.py` + `appid.py` (game names -> appids), `schema.py` (validation) |
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

## How extraction works (no LLM)

`whitespace extract` turns a post into structured wants in four deterministic steps:

1. **Prefilter** (`prefilter.py`): regex triggers ("I wish", "kill for", "games like", ...). Only
   matching posts go further.
2. **Dependency parse + markers** (`nlp.py`): spaCy finds the wanted noun ("farming sim"), then
   `with X` / `without X` / `no X` phrases become feature tuples, with filler ("honestly",
   "garbage") stripped. One item per tuple, so *"kill for a farming sim ... without the energy timer
   garbage"* becomes `farming sim without energy timer` and `farming sim with co-op`. A small lexicon
   catches things the parser misses (e.g. "my partner can play too" -> co-op).
   This replaces OpenIE/CoreNLP, which needs Java. It's the same idea (subject/relation/object
   tuples) built on the spaCy parse.
3. **Gazetteer NER** (`EntityRuler`): game names from `config/games.yaml` (+ optional bulk
   `data/apps.json`) are tagged and resolved to Steam appids. Multi-word names match any case;
   single-word names ("Hades") match case-sensitively to avoid tagging ordinary words.
4. **Intensity** (`rules.py`): strong phrases ("kill for", "desperately") or a strong VADER score ->
   3; moderate verbs ("wish", "looking for") -> 2; neutral phrasing -> 1. The request wording itself
   is stripped before scoring so "would be great if" doesn't count as emotion.

Known limits (measure these on a gold set before trusting the output):
- The small English parser is noisy; rules anchor on markers rather than exact dependency labels.
- Wishes phrased as a relative clause with no marker ("a game where you run a bakery") are missed.
- `confidence` is a heuristic score, not a probability.
- Clustering still uses a small local embedding model (MiniLM), not an LLM. Swap in TF-IDF if you
  want zero neural models.

## Adding a source

1. Read its ToS and robots.txt; add an entry to `config/sources.yaml` and fill `tos_reviewed`.
2. Prefer an official API. Otherwise subclass `SourceCrawler` (see `forum.py` for the Scrapling pattern).
3. Implement `fetch_since(cursor)` and a pure `parse(item)`. Save a small HTML/JSON fixture and write
   tests against it, so the parser is testable without the network.
4. Wire it in `cli.py::cmd_ingest`.

## Notes

- Use `StealthyFetcher` only where a site's ToS allows automated access. Never use it to bypass logins.
- Raw scraped data and the SQLite file stay out of git. Publish aggregates and short quotes only.
- `Dockerfile` browser install and the compose/CI files were written without running Docker in the
  authoring environment, so treat the first `make build` as the real test.
