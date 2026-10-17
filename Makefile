.PHONY: build test lint fmt shell stats ingest clean-posts extract dashboard
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

extract:      ## spaCy rule extraction, no API keys
	$(RUN) app extract --limit 200

dashboard:    ## http://localhost:8501
	docker compose --profile dashboard up --build dashboard
