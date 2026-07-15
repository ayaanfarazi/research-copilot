"""
Credit-brief Streamlit app — Phase 3, Step 2 (deterministic layer only).

This app is a PURE VIEW over a cached Brief. It never blocks on live API work:
it calls assemble_brief(ticker, use_cache=True) and reads the disk cache warmed by
the assembler gate. The six LLM panels on the Brief are intentionally not rendered
yet — that is the next packet. Every number here flows through render_figure, so
each figure carries an expand-to-source drill-down.
"""

from __future__ import annotations

import sys

sys.path.insert(0, ".")

import streamlit as st

from src.brief import assemble_brief, has_cached_brief
from src.ui.render import (
    render_covenant_panel,
    render_credit_panel,
    render_degraded_status,
    render_ebitda_bridge,
    render_header,
    render_operating_panel,
    render_survival_panel,
)

DEMO_TICKERS = ["MSFT", "VZ", "MCD", "NVDA", "CRM"]

st.set_page_config(page_title="Credit Brief — deterministic layer", layout="wide")

# --- Sidebar: ticker selector over the five demo companies ------------------
st.sidebar.title("Credit Brief")
st.sidebar.caption("Deterministic layer (Phase 3, Step 2)")
ticker = st.sidebar.selectbox("Company", DEMO_TICKERS, index=0)
st.sidebar.caption(
    "Pure view over a cached Brief — no live API calls. "
    "Cache is warmed by scripts/verify_brief.py."
)

# --- Data load: cache-only, never a blank page on failure -------------------
# Guard the pure-view contract: if this ticker's brief isn't warmed, tell the
# user how to warm it rather than silently kicking off a multi-minute API rebuild.
if not has_cached_brief(ticker):
    st.title(f"{ticker}")
    st.info(
        f"No cached brief for **{ticker}** yet. This app is a pure view over a "
        f"pre-assembled Brief and will not run live API calls.\n\n"
        f"Warm the cache first:\n\n```\npython scripts/verify_brief.py {ticker}\n```"
    )
    st.stop()

try:
    brief = assemble_brief(ticker, use_cache=True)
except Exception as exc:  # noqa: BLE001 — surface any load failure as a visible error
    st.error(
        f"Could not load a cached brief for **{ticker}**.\n\n"
        f"`{type(exc).__name__}: {exc}`\n\n"
        f"Warm the cache first with: `python scripts/verify_brief.py {ticker}`"
    )
    st.stop()

# --- Header + scorecard band ------------------------------------------------
render_header(brief)

# --- Graceful degradation: no industrial brief for this filer ---------------
if brief.fin.status != "ok":
    render_degraded_status(brief)
    st.stop()

st.divider()

# --- Deterministic panels ---------------------------------------------------
left, right = st.columns(2)
with left:
    render_credit_panel(brief)
    render_survival_panel(brief)
with right:
    render_ebitda_bridge(brief)
    render_covenant_panel(brief)
    render_operating_panel(brief)

st.divider()
st.caption(
    "Deterministic figures only. Every value expands to its source (XBRL tag / "
    "period / accession for facts; formula / components / reconciliation for "
    "computed metrics). LLM panels are rendered in the next step."
)
