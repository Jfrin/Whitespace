"""Streamlit dashboard: streamlit run src/whitespace/serve/app.py"""

import os
import sqlite3

import pandas as pd
import streamlit as st

DB = os.path.join(os.environ.get("WHITESPACE_DATA_DIR", "data"), "whitespace.sqlite")

st.title("Game niche explorer")
conn = sqlite3.connect(DB)
df = pd.read_sql_query(
    "SELECT c.name, c.size, n.demand, n.dissatisfaction, n.supply_pressure, n.gap_score "
    "FROM niche_score n JOIN cluster c ON c.id = n.cluster_id ORDER BY n.gap_score DESC",
    conn,
)
if df.empty:
    st.info("No scored niches yet. Run the pipeline: `make pipeline`.")
else:
    st.dataframe(df, use_container_width=True)
    # TODO: trend lines, representative quotes, closest existing games
