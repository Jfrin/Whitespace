.PHONY: gold-export gold-score build test lint fmt shell stats ingest clean-posts extract catalog context dashboard
RUN = docker compose run --rm

build:        ## build all images
	docker compose --profile dev --profile pipeline --profile dashboard build

test:         ## run pytest (with coverage) in Docker
	$(RUN) test

lint:         ## ruff check + format check
	$(RUN) lint

fmt:          ## auto-fix lint + format
	$(RUN) fmt

shell:        ## shell in the dev image
	$(RUN) --entrypoint sh test

stats:
	$(RUN) app stats

ingest:       ## make ingest SOURCE=steam_reviews
	$(RUN) app ingest $(SOURCE)

clean-posts:
	$(RUN) app clean

extract:      ## Gemini extraction (GEMINI_API_KEY in .env); --extractor rules for offline
	$(RUN) app extract --limit 200

catalog:      ## fetch genres for referenced games
	$(RUN) app catalog

context:      ## attach game/genre/scope to wants
	$(RUN) app context

gold-export:   ## sample wants to label by hand -> data/gold_items.csv
	$(RUN) app gold-export

gold-score:    ## precision from the labeled CSV
	$(RUN) app gold-score

dashboard:    ## http://localhost:8501
	docker compose --profile dashboard up --build dashboard
