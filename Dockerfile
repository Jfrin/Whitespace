# syntax=docker/dockerfile:1
# Targets: dev (tests/lint) | prod (CLI pipeline) | dashboard (Streamlit)
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
WORKDIR /app
RUN useradd --create-home --uid 1000 app && mkdir -p /app/data && chown -R app:app /app
COPY --chown=app:app pyproject.toml README.md ./
COPY --chown=app:app src ./src

# ---- dev: editable install + test/lint tooling ------------------------------------------
FROM base AS dev
RUN pip install -e ".[dev,scrape,nlp]" && python -m spacy download en_core_web_sm
COPY --chown=app:app tests ./tests
COPY --chown=app:app config ./config
USER app
CMD ["pytest"]

# ---- prod: the pipeline CLI -------------------------------------------------------------
# Build with --build-arg WITH_BROWSERS=1 for DynamicFetcher/StealthyFetcher sources
# (downloads Playwright browsers, adds ~500MB+). Static HTML + APIs don't need it.
FROM base AS prod
ARG EXTRAS=scrape,nlp
ARG WITH_BROWSERS=0
RUN pip install ".[${EXTRAS}]" \
    && if python -c 'import spacy' 2>/dev/null; then python -m spacy download en_core_web_sm; fi \
    && if [ "$WITH_BROWSERS" = "1" ]; then scrapling install && chmod -R a+rX /ms-playwright; fi
COPY --chown=app:app config ./config
USER app
ENTRYPOINT ["whitespace"]
CMD ["stats"]

# ---- dashboard --------------------------------------------------------------------------
FROM base AS dashboard
RUN pip install ".[dashboard]"
USER app
EXPOSE 8501
CMD ["streamlit", "run", "src/whitespace/serve/app.py", "--server.address=0.0.0.0", "--server.port=8501"]
