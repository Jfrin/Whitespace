"""Local Streamlit console for running the pipeline and viewing its results."""

import os
import sqlite3
from urllib.parse import urlparse

import pandas as pd
import streamlit as st

from whitespace.config import Settings, load_sources
from whitespace.serve.jobs import JobRunner, commands, parse_appids
from whitespace.storage import Database

st.set_page_config(page_title="Whitespace", page_icon="🎮", layout="wide")


@st.cache_resource
def runner() -> JobRunner:
    return JobRunner()


st.title("Whitespace")
st.caption("Find what players want. Run the pipeline and explore the results here.")

with st.sidebar:
    st.header("Settings")
    default_appids = load_sources()["steam_reviews"].options.get("appids", [])
    appids_input = st.text_input(
        "Steam app IDs",
        value=", ".join(map(str, default_appids)),
        help="Comma-separated IDs from Steam store URLs.",
    )
    contact = st.text_input(
        "Contact email",
        value=os.environ.get("WHITESPACE_CONTACT", ""),
        help="Included in requests to source sites.",
    )
    extractor = st.selectbox("Extractor", ["azure", "rules"], format_func=str.title)
    limit = st.number_input("Maximum posts to extract", min_value=1, value=100, step=50)
    if extractor == "azure":
        endpoint = st.text_input(
            "Azure endpoint", value=os.environ.get("AZURE_OPENAI_ENDPOINT", "")
        )
        api_key = st.text_input(
            "Azure API key", value=os.environ.get("AZURE_OPENAI_API_KEY", ""), type="password"
        )
        deployment = st.text_input(
            "Deployment name", value=os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1-mini")
        )
        rpm = st.number_input("Requests per minute", min_value=1, value=10)
        batch = st.number_input("Posts per request", min_value=1, max_value=100, value=8)
        st.caption("Azure requests may incur charges.")
    else:
        st.caption(
            "Rules run offline and only process English posts; "
            "other languages are skipped, not deleted."
        )
    st.caption("Changes apply to this browser session; they don't modify .env or config files.")

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
    busy = status == "running"
    st.subheader("Run pipeline")
    st.caption("Full pipeline: ingest Steam reviews → clean → extract → catalog → add context.")
    if st.button("▶ Run full pipeline", type="primary", disabled=busy):
        start_job("pipeline")
    cols = st.columns(5)
    for col, stage, label in zip(
        cols,
        ["ingest", "clean", "extract", "catalog", "context"],
        ["Ingest", "Clean", "Extract", "Catalog", "Context"],
        strict=True,
    ):
        if col.button(label, disabled=busy, use_container_width=True):
            start_job(stage)
    if status == "running":
        st.info("Running… progress updates automatically. You can leave this page open.")
    elif status == "completed":
        st.success("Finished. Refresh results below to see the latest data.")
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
        wants = pd.read_sql_query(
            "SELECT w.want_summary AS want, w.request_type AS type, w.subject, w.quote, "
            "w.confidence, w.context_genres AS genres, p.lang AS language "
            "FROM want_item w JOIN post p ON p.id = w.post_id ORDER BY w.id DESC LIMIT 100",
            db.conn,
        )
        if wants.empty:
            st.info("No wants yet. Run the pipeline or start with Ingest, Clean, and Extract.")
        else:
            st.caption("Most recent 100 extracted wants and their source quotes.")
            st.dataframe(wants, use_container_width=True, hide_index=True)
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
