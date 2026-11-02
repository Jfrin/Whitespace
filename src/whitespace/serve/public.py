"""Standalone, read-only results explorer for a packaged demo dataset."""

from pathlib import Path

import streamlit as st

from whitespace.serve.public_data import filter_items, load_demo

DATASET = Path(__file__).resolve().parents[3] / "demo" / "public.json"

st.set_page_config(page_title="Whitespace | Explore player feedback", page_icon="🎮", layout="wide")
st.title("Whitespace")
st.caption("Explore what players want from games.")

try:
    label, description, items = load_demo(DATASET)
except (OSError, ValueError) as exc:
    st.error(f"The demo dataset could not be loaded: {exc}")
    st.stop()

st.info(f"{label}: {description}")
with st.sidebar:
    st.header("Explore")
    games = st.multiselect("Game", sorted({item["game"] for item in items}))
    genres = st.multiselect("Genre", sorted({genre for item in items for genre in item["genres"]}))
    types = st.multiselect("Request type", sorted({item["request_type"] for item in items}))
    confidence = st.slider("Minimum confidence", 0.0, 1.0, 0.0, 0.1)
    st.caption("Confidence is a heuristic, not a probability.")

filtered = filter_items(items, games, genres, types, confidence)
cols = st.columns(3)
cols[0].metric("Matching examples", len(filtered))
cols[1].metric("Games", len({item["game"] for item in filtered}))
cols[2].metric("Genres", len({genre for item in filtered for genre in item["genres"]}))

st.subheader("Explore feedback")
if not filtered:
    st.info("No examples match these filters.")
for item in filtered:
    with st.container(border=True):
        st.subheader(item["summary"])
        st.caption(
            f"{item['game']} · {', '.join(item['genres'])} · "
            f"{item['request_type'].replace('_', ' ')} · {item['subject']} · "
            f"confidence {item['confidence']:.2f}"
        )
        st.text(f"“{item['quote']}”")

st.divider()
st.subheader("How it works")
st.write(
    "Whitespace collects public feedback, identifies player wants and complaints, "
    "and connects them to games and genres. This demo uses fictional examples."
)
st.caption("Clustering and supply-gap scoring are planned, not yet available.")
st.link_button("View project on GitHub", "https://github.com/Jfrin/Whitespace")
