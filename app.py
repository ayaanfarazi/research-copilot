"""
Credit-brief Streamlit app — Phase 3, Step 4 (Investor/Credit view toggle).

This app is a PURE VIEW over a cached Brief. It never blocks on live API work:
it calls assemble_brief(ticker, use_cache=True) and reads the disk cache warmed by
the assembler gate. Every number flows through render_figure, so each figure
carries an expand-to-source drill-down.

The Investor/Credit toggle changes ONLY ordering and emphasis (build_plan.md §4):
the underlying Brief, every figure, and every panel body are identical across
views — there is one set of panel-render calls, sequenced by a per-view list of
section keys. Nothing is recomputed and no API is called when the view changes.
"""

from __future__ import annotations

import sys

sys.path.insert(0, ".")

import streamlit as st

from src.brief import assemble_brief, has_cached_brief
from src.ui.render import (
    begin_render_run,
    render_addback_panel,
    render_business_summary_panel,
    render_covenant_panel,
    render_credit_panel,
    render_degraded_status,
    render_ebitda_bridge,
    render_header,
    render_operating_panel,
    render_qoe_candidates_panel,
    render_revenue_drivers_panel,
    render_risks_panel,
    render_scorecard_band,
    render_survival_panel,
    render_synthesis_panel,
)

DEMO_TICKERS = ["MSFT", "VZ", "MCD", "NVDA", "CRM"]

st.set_page_config(page_title="Credit Brief", layout="wide")

# Reset the drill-down widget-key registry at the very top of every script run so
# the progressive source-trace buttons get stable keys (see render.begin_render_run).
begin_render_run()

# --- Sidebar: ticker selector over the five demo companies ------------------
st.sidebar.title("Credit Brief")
st.sidebar.caption("Deterministic figures + AI panels (Phase 3, Step 4)")
ticker = st.sidebar.selectbox("Company", DEMO_TICKERS, index=0)
view = st.sidebar.radio("View", ["Credit", "Investor"], index=0, key="view")
st.sidebar.caption(
    "The view toggle reorders panels only — same cached Brief, no recompute, "
    "no API. Cache is warmed by scripts/verify_brief.py."
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

# --- Header ------------------------------------------------------------------
render_header(brief)

# --- Graceful degradation: no industrial brief for this filer ---------------
if brief.fin.status != "ok":
    render_degraded_status(brief)
    st.stop()

# --- One set of panel-render calls, keyed by section -------------------------
# Each panel body is unchanged from the previous packet. The view toggle only
# picks the ORDER these are called in; the Brief and every figure are identical.
SECTIONS = {
    "scorecard": render_scorecard_band,           # 🏦 Credit scorecard
    "synthesis": render_synthesis_panel,          # 🧭 Panel A — anchored synthesis
    "addback": render_addback_panel,              # ⚖️ Panel B — add-back adversary
    "credit": render_credit_panel,                # Credit & capital structure
    "ebitda": render_ebitda_bridge,               # EBITDA bridge
    "survival": render_survival_panel,            # Survival / maturity wall
    "covenant": render_covenant_panel,            # Covenant screen
    "qoe": render_qoe_candidates_panel,           # QoE candidates
    "business": render_business_summary_panel,    # Business summary
    "operating": render_operating_panel,          # Operating performance
    "revenue_drivers": render_revenue_drivers_panel,
    "risks": render_risks_panel,                  # Company-specific risks
}

# Per-view ordering (build_plan.md §4). Every section appears in both views —
# the toggle changes sequence/emphasis, never which panels are reachable.
VIEW_ORDER = {
    # Credit: lead with the credit verdict; investor-facing panels support below.
    "Credit": [
        "scorecard", "synthesis", "credit", "ebitda", "survival", "covenant",
        "addback", "qoe",
        "business", "risks", "revenue_drivers", "operating",
    ],
    # Investor: lead with the equity story; credit machinery supports below.
    "Investor": [
        "business", "operating", "revenue_drivers",
        "credit", "ebitda", "survival", "scorecard", "covenant",
        "synthesis", "addback", "qoe", "risks",
    ],
}

st.caption(
    f"**{view} view** — panels reordered for emphasis only. Same cached Brief, "
    f"zero recompute. Every value expands to the same source drill-down."
)
st.divider()

for i, key in enumerate(VIEW_ORDER[view]):
    if i:
        st.divider()
    SECTIONS[key](brief)

st.divider()
st.caption(
    "Every number on this page carries its own source trail: computed figures show "
    "the plain-English recipe, and each input opens to the exact filing, period, and "
    "XBRL tag — with a link to the filing on SEC.gov."
)
