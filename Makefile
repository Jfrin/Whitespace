# Whitespace shortcuts. Run `make help`.
# Recipes start with ">" instead of a tab (.RECIPEPREFIX), so this survives copy-paste.
.RECIPEPREFIX = >
.DEFAULT_GOAL := help

RUN    := docker compose run --rm
SOURCE ?= steam_reviews
KIND   ?= items
N      ?= 300
LIMIT  ?=

.PHONY: help build build-app test lint fmt shell ingest clean-posts extract extract-rules redo \
        catalog context refresh stats gemini-models gold-export gold-score pipeline dashboard

help:
> @echo Dev:       make build / test / lint / fmt / shell
> @echo Pipeline:  make ingest [SOURCE=steam_reviews] / clean-posts / extract [LIMIT=100]
> @echo            make catalog / context / stats / pipeline (runs all of the above in order)
> @echo Redo:      make redo [LIMIT=100] (re-extract all) / refresh (recompute context)
> @echo            make extract-rules (offline spaCy, replaces existing wants)
> @echo Gemini:    make gemini-models
> @echo Gold set:  make gold-export [KIND=items/missed/untriggered N=300] / gold-score [KIND=...]
> @echo Dashboard: make dashboard

# --- build / dev ---------------------------------------------------------------------------
build:
> docker compose --profile dev --profile pipeline --profile dashboard build

build-app:
> docker compose --profile pipeline build app

test:
> $(RUN) test

lint:
> $(RUN) lint

fmt:
> $(RUN) fmt

shell:
> $(RUN) --entrypoint sh test

# --- pipeline (each rebuilds the app image first, because `docker compose run` does not) ---
ingest: build-app
> $(RUN) app ingest $(SOURCE)

clean-posts: build-app
> $(RUN) app clean

extract: build-app
> $(RUN) app extract $(if $(LIMIT),--limit $(LIMIT),)

redo: build-app
> $(RUN) app extract --redo $(if $(LIMIT),--limit $(LIMIT),)

extract-rules: build-app
> $(RUN) app extract --extractor rules --redo

catalog: build-app
> $(RUN) app catalog

context: build-app
> $(RUN) app context

refresh: build-app
> $(RUN) app context --refresh

stats: build-app
> $(RUN) app stats

gemini-models: build-app
> $(RUN) app gemini-models

gold-export: build-app
> $(RUN) app gold-export --kind $(KIND) --n $(N)

gold-score: build-app
> $(RUN) app gold-score --kind $(KIND)

pipeline: ingest clean-posts extract catalog context stats

dashboard:
> docker compose --profile dashboard up --build dashboard
