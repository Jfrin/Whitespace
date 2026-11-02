"""Local Streamlit console for running the pipeline and viewing its results."""

import os
import sqlite3
from math import ceil
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
import streamlit as st

from whitespace.config import Settings, load_sources
from whitespace.extract.quota import DEFAULT_RPM, DEFAULT_TPM
from whitespace.serve.explorer import (
    Filters,
    count,
    engagement_text,
    game_coverage,
    options,
    page,
    review_engagement,
    safe_source_url,
    string_list,
)
from whitespace.serve.jobs import JobRunner, commands, parse_appids
from whitespace.serve.settings import read_settings, save_settings
from whitespace.storage import Database

st.set_page_config(page_title="Whitespace", page_icon="🎮", layout="wide")


@st.cache_resource
def runner() -> JobRunner:
    return JobRunner()


st.title("Whitespace")
st.caption("Find what players want. Run the pipeline and explore the results here.")

env_path = Path(os.environ.get("WHITESPACE_UI_ENV_PATH", ".env"))
sources_path = Path(os.environ.get("WHITESPACE_UI_SOURCES_PATH", "config/sources.yaml"))
saved = read_settings(env_path)


def default(name: str, fallback: str = "") -> str:
    return saved.get(name, os.environ.get(name, fallback))


def number_default(name: str, fallback: int, maximum: int | None = None) -> int:
    try:
        number = int(default(name, str(fallback)))
        return max(1, min(number, maximum)) if maximum else max(1, number)
    except ValueError:
        return fallback


with st.sidebar:
    st.header("Settings")
    default_appids = load_sources(sources_path)["steam_reviews"].options.get("appids", [])
    appids_input = st.text_input(
        "Steam app IDs",
        value=", ".join(map(str, default_appids)),
        help="Comma-separated IDs from Steam store URLs.",
    )
    contact = st.text_input(
        "Contact email",
        value=default("WHITESPACE_CONTACT"),
        help="Included in requests to source sites.",
    )
    extractor_options = ["azure", "rules"]
    saved_extractor = default("WHITESPACE_UI_EXTRACTOR", "azure")
    extractor = st.selectbox(
        "Extractor",
        extractor_options,
        index=extractor_options.index(saved_extractor)
        if saved_extractor in extractor_options
        else 0,
        format_func=str.title,
    )
    limit = st.number_input(
        "Maximum posts to extract",
        min_value=1,
        value=number_default("WHITESPACE_UI_LIMIT", 100),
        step=50,
    )
    if extractor == "azure":
        endpoint = st.text_input("Azure endpoint", value=default("AZURE_OPENAI_ENDPOINT"))
        api_key = st.text_input(
            "Azure API key", value=default("AZURE_OPENAI_API_KEY"), type="password"
        )
        deployment = st.text_input(
            "Deployment name", value=default("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1-mini")
        )
        rpm = st.number_input(
            "Requests per minute",
            min_value=1,
            value=number_default("AZURE_OPENAI_RPM", DEFAULT_RPM),
        )
        tpm = st.number_input(
            "Tokens per minute",
            min_value=1,
            value=number_default("AZURE_OPENAI_TPM", DEFAULT_TPM),
            help="Per-process estimated token budget; Azure may apply different limits.",
        )
        batch = st.number_input(
            "Posts per request",
            min_value=1,
            max_value=100,
            value=number_default("AZURE_OPENAI_BATCH_SIZE", 8, 100),
        )
        st.caption("Azure requests may incur charges.")
    else:
        st.caption(
            "Rules run offline and only process English posts; "
            "other languages are skipped, not deleted."
        )
    if st.button("Save settings"):
        try:
            ids = parse_appids(appids_input)
            updates = {
                "WHITESPACE_CONTACT": contact.strip(),
                "WHITESPACE_UI_EXTRACTOR": extractor,
                "WHITESPACE_UI_LIMIT": str(limit),
            }
            if extractor == "azure":
                updates.update(
                    {
                        "AZURE_OPENAI_ENDPOINT": endpoint.strip(),
                        "AZURE_OPENAI_API_KEY": api_key.strip(),
                        "AZURE_OPENAI_DEPLOYMENT": deployment.strip(),
                        "AZURE_OPENAI_RPM": str(rpm),
                        "AZURE_OPENAI_TPM": str(tpm),
                        "AZURE_OPENAI_BATCH_SIZE": str(batch),
                    }
                )
            save_settings(env_path, sources_path, ids, updates)
            st.success("Settings saved to .env and config/sources.yaml.")
        except (OSError, ValueError) as exc:
            st.error(f"Could not save settings: {exc}")
    st.caption(
        "Save settings writes to .env and config/sources.yaml. The API key is stored in plaintext."
    )

job = runner()


def start_job(stage: str) -> None:
    try:
        appids = parse_appids(appids_input)
        env = os.environ.copy()
        if contact.strip():
            env["WHITESPACE_CONTACT"] = contact.strip()
        if extractor == "azure" and stage in {"pipeline", "extract"}:
            url = urlparse(endpoint.strip())
            if url.scheme != "https" or not url.hostname or url.username or url.password:
                raise ValueError("Enter a valid HTTPS Azure endpoint.")
            if url.query or url.fragment or not api_key.strip() or not deployment.strip():
                raise ValueError("Enter an Azure API key and deployment, with no query in the URL.")
            env.update(
                {
                    "AZURE_OPENAI_ENDPOINT": endpoint.strip(),
                    "AZURE_OPENAI_API_KEY": api_key.strip(),
                    "AZURE_OPENAI_DEPLOYMENT": deployment.strip(),
                    "AZURE_OPENAI_RPM": str(rpm),
                    "AZURE_OPENAI_TPM": str(tpm),
                    "AZURE_OPENAI_BATCH_SIZE": str(batch),
                }
            )
        if job.start(commands(stage, "steam_reviews", extractor, int(limit)), appids, env):
            st.rerun()
        st.warning("Another job is already running.")
    except ValueError as exc:
        st.error(str(exc))


@st.fragment(run_every=2)
def controls() -> None:
    status, output = job.snapshot()
    busy = status in {"running", "stopping"}
    st.subheader("Run pipeline")
    st.caption("Full pipeline: ingest Steam reviews → clean → extract → catalog → add context.")
    if st.button("▶ Run full pipeline", type="primary", disabled=busy):
        start_job("pipeline")
    if st.button("■ Stop run", disabled=status != "running"):
        if job.stop():
            st.rerun()
    cols = st.columns(7)
    for col, stage, label in zip(
        cols,
        ["ingest", "clean", "extract", "catalog", "context", "steam-tags", "review-metrics"],
        ["Ingest", "Clean", "Extract", "Catalog", "Context", "Steam tags", "Review metrics"],
        strict=True,
    ):
        if col.button(label, disabled=busy, use_container_width=True):
            start_job(stage)
    if status == "running":
        st.info("Running… Stop ends the current stage and skips the rest.")
    elif status == "stopping":
        st.info("Stopping the current stage…")
    elif status == "completed":
        st.success("Finished. Refresh results below to see the latest data.")
    elif status == "stopped":
        st.warning("Stopped. Completed work is saved; you can resume by running the stage again.")
    elif status == "failed":
        st.error("The run stopped. Check the output below.")
    if output:
        st.text_area("Run output", value=output, height=260, disabled=True)


controls()

st.divider()
st.subheader("Results")
st.button("Refresh results")
try:
    db = Database(Settings.from_env().db_path)
    try:
        cols = st.columns(3)
        for col, label, table in zip(
            cols,
            ["Posts", "Extracted wants", "Games in catalog"],
            ["post", "want_item", "catalog_game"],
            strict=True,
        ):
            col.metric(label, db.count(table))
        try:
            configured_ids = parse_appids(appids_input)
        except ValueError:
            configured_ids = default_appids
        coverage = game_coverage(db.conn, configured_ids)
        st.subheader("Games and extraction coverage")
        st.caption(
            "Ingested posts are not the same as extracted wants. The extraction limit is per run "
            "across all games. Run Catalog to fetch missing game names and genres."
        )
        st.dataframe(
            [
                {
                    "Game": row["name"] or f"Steam app {row['appid']}",
                    "App ID": row["appid"],
                    "Posts": row["posts"],
                    "Extracted items": row["wants"],
                }
                for row in coverage
            ],
            use_container_width=True,
            hide_index=True,
        )
        choices = options(db.conn)
        total = count(db.conn, Filters())
        if not total:
            st.info("No wants yet. Run the pipeline or start with Ingest, Clean, and Extract.")
        else:
            st.caption("Filter all extracted items. Confidence is a heuristic, not a probability.")
            search = st.text_input("Search summaries and quotes")
            left, right = st.columns(2)
            games = left.multiselect(
                "Game", list(choices["games"]), format_func=choices["games"].get
            )
            genres = right.multiselect("Genre / game type", choices["genres"])
            user_tags = right.multiselect("Steam user-defined tags", choices["user_tags"])
            subjects = left.multiselect("Subject", choices["subjects"])
            types = right.multiselect("Request type", choices["request_types"])
            order = st.selectbox("Sort results", ["Newest", "Most helpful", "Most funny"])
            confidence = st.slider("Minimum confidence", 0.0, 1.0, 0.0, 0.1)
            st.caption("User-defined tags describe the reviewed game, not individual requests.")
            sort_by = {"Newest": "newest", "Most helpful": "helpful", "Most funny": "funny"}[order]
            filters = Filters(
                games, genres, subjects, types, search, confidence, user_tags, sort_by
            )
            matches = count(db.conn, filters)
            st.caption(f"{matches:,} matching extracted items out of {total:,} total")
            if not matches:
                st.info("No results match these filters.")
            else:
                page_size = 25
                pages = ceil(matches / page_size)
                st.session_state["results_page"] = min(
                    st.session_state.get("results_page", 1), pages
                )
                current = st.number_input(
                    "Page",
                    min_value=1,
                    max_value=pages,
                    key="results_page",
                    help=f"{page_size} items per page, newest first.",
                )
                for item in page(db.conn, filters, current, page_size):
                    with st.container(border=True):
                        st.subheader(item["want_summary"] or "Unknown summary")
                        game = item["game_name"] or (
                            f"Unknown game ({item['game_appid']})"
                            if item["game_appid"]
                            else "Unknown game"
                        )
                        genre_text = (
                            ", ".join(string_list(item["context_genres"])) or "Unknown genre"
                        )
                        st.caption(f"{game} · {genre_text}")
                        tags = string_list(item["user_tags"])
                        if tags:
                            st.caption("Steam user-defined tags: " + ", ".join(tags))
                        conf = (
                            f"{item['confidence']:.2f}"
                            if item["confidence"] is not None
                            else "unknown"
                        )
                        st.caption(
                            f"{item['request_type'] or 'Unknown type'} · "
                            f"{item['subject'] or 'Unknown subject'} · confidence {conf} · "
                            f"{item['extractor_version'] or 'Unknown extractor'}"
                        )
                        st.text(item["quote"] or "Quote unavailable")
                        engagement = review_engagement(item["source"], item["post_meta"])
                        for label in engagement_text(engagement):
                            st.caption(label)
                        observed = engagement.get("observed_at") or item["raw_fetched_at"]
                        if engagement and observed:
                            st.caption(
                                f"Latest metric update: {observed}; some values may be older."
                            )
                        references = string_list(item["referenced_games"])
                        if references:
                            st.caption("Compared with: " + ", ".join(references))
                        if url := safe_source_url(item["url"]):
                            st.link_button("Source", url)
                        else:
                            st.caption("Source link unavailable")
        niches = pd.read_sql_query(
            "SELECT c.name, c.size, n.demand, n.dissatisfaction, n.supply_pressure, n.gap_score "
            "FROM niche_score n JOIN cluster c ON c.id = n.cluster_id ORDER BY n.gap_score DESC",
            db.conn,
        )
        if not niches.empty:
            st.subheader("Scored niches")
            st.dataframe(niches, use_container_width=True, hide_index=True)
        else:
            st.caption("Niche clustering and scoring are not yet available in the pipeline.")
    finally:
        db.conn.close()
except (OSError, ValueError, sqlite3.Error) as exc:
    st.error(f"Could not open the data directory: {exc}")
