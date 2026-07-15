"""
Deterministic-layer renderers for the credit brief (Phase 3, Step 2).

The credibility money-shot lives here: `render_figure` shows a value and an
expand-to-source drill-down exposing full provenance (figure_id, confidence tier
badge, and — per figure kind — XBRL tag/period/accession or formula/components/
breakdown/notes). EVERY number the app prints goes through it, so nothing is ever
an un-sourced float. The same helper will carry LLM claim→source in the next packet.

This module renders the DETERMINISTIC layer only (Phase 1 figures). The six LLM
panels on the Brief are intentionally not rendered yet.

Everything is graceful-degradation-first: a missing figure renders an explicit
"not found — see filing", never a blank or a silent 0.
"""

from __future__ import annotations

import streamlit as st

from src.brief import Brief
from src.data.models import ConfidenceTier, make_figure_id

# ---------------------------------------------------------------------------
# Confidence badges (build_plan.md §6 component 3)
#   VERIFIED -> green, HIGH -> no badge (the default), LOW -> amber "check source",
#   NOT_FOUND -> red "see filing".
# ---------------------------------------------------------------------------
_TIER_BADGE = {
    ConfidenceTier.VERIFIED: ":green[**✓ VERIFIED**]",
    ConfidenceTier.HIGH: None,
    ConfidenceTier.LOW: ":orange[**⚠ check source**]",
    ConfidenceTier.NOT_FOUND: ":red[**✗ see filing**]",
}

# Credit-band label -> Streamlit markdown color.
_BAND_COLOR = {
    "strong": "green",
    "adequate": "blue",
    "stretched": "orange",
    "distressed": "red",
}

# Covenant screen flag -> color.
_FLAG_COLOR = {"green": "green", "amber": "orange", "red": "red", "unknown": "gray"}


# ---------------------------------------------------------------------------
# Value formatting
# ---------------------------------------------------------------------------

def format_usd(v: float) -> str:
    a = abs(v)
    sign = "-" if v < 0 else ""
    if a >= 1e9:
        return f"{sign}${a / 1e9:,.2f}B"
    if a >= 1e6:
        return f"{sign}${a / 1e6:,.1f}M"
    return f"{sign}${a:,.0f}"


def format_value(fig: object) -> str:
    """Human value for a figure, unit-aware. '—' when there is no number."""
    val = getattr(fig, "value", None)
    if val is None:
        return "—"
    unit = getattr(fig, "unit", None)
    if unit == "USD":
        return format_usd(val)
    if unit == "x":
        return f"{val:,.2f}x"
    if unit == "%":
        return f"{val:,.1f}%"
    if unit and unit.startswith("severity"):
        return getattr(fig, "label", None) or f"{val:g}"
    return f"{val:,.2f}"


def _headline(fig: object) -> str:
    """Headline string: prefers a categorical label, appends the number when useful."""
    label = getattr(fig, "label", None)
    val = getattr(fig, "value", None)
    unit = getattr(fig, "unit", None) or ""
    fval = format_value(fig)
    if label:
        if val is not None and not unit.startswith("severity"):
            return f"{label} ({fval})"
        return label
    return fval


# Non-"ok" computed statuses get an explicit, colored inline tag (never a blank).
_STATUS_TAG = {
    "not_found": ":red[not found]",
    "not_meaningful": ":orange[not meaningful]",
    "net_cash": ":green[net cash]",
    "anomaly": ":red[anomaly]",
}


# ---------------------------------------------------------------------------
# The reusable provenance helper
# ---------------------------------------------------------------------------

def render_figure(brief: Brief, concept: str, year: int, label: str | None = None) -> object | None:
    """Render one figure as `label: value <badge>` with an expand-to-source panel.

    Returns the underlying figure object (or None if absent) so callers can chain.
    Every rendered number in the app must go through this function.
    """
    figure_id = make_figure_id(concept, year)
    fig = brief.fin.figures.get(figure_id)
    display_label = label or concept.replace("_", " ").title()

    if fig is None:
        st.markdown(f"**{display_label}:** :red[not found — see filing]")
        with st.expander("🔍 source"):
            st.markdown(f"- **figure_id:** `{figure_id}`")
            st.markdown("- no figure was produced for this concept and year")
        return None

    return render_figure_object(brief, fig, display_label)


def render_figure_by_id(brief: Brief, figure_id: str, label: str | None = None) -> object | None:
    """Same as render_figure but keyed directly by figure_id (used for bridge rows)."""
    fig = brief.fin.figures.get(figure_id)
    display_label = label or figure_id
    if fig is None:
        st.markdown(f"**{display_label}:** :red[not found — see filing]")
        with st.expander("🔍 source"):
            st.markdown(f"- **figure_id:** `{figure_id}` (not present)")
        return None
    return render_figure_object(brief, fig, display_label)


def render_figure_object(brief: Brief, fig: object, display_label: str) -> object:
    """Render a resolved figure object with its confidence badge + source expander."""
    headline = _headline(fig)
    badge = _TIER_BADGE.get(getattr(fig, "confidence", None))
    status = getattr(fig, "status", "ok")
    status_tag = _STATUS_TAG.get(status) if status not in ("ok", None) else None

    parts = [f"**{display_label}:** {headline}"]
    if status_tag:
        parts.append(status_tag)
    if badge:
        parts.append(badge)
    st.markdown("  ".join(parts))

    with st.expander("🔍 source"):
        _render_provenance(fig)
    return fig


def _render_provenance(fig: object) -> None:
    """Dump every provenance field the figure carries (kind-aware)."""
    st.markdown(f"- **figure_id:** `{fig.figure_id}`")

    val = getattr(fig, "value", None)
    unit = getattr(fig, "unit", None) or ""
    if val is not None:
        st.markdown(f"- **value:** {val:,} {unit}".rstrip())
    else:
        st.markdown("- **value:** (none)")

    conf = getattr(fig, "confidence", None)
    if conf is not None:
        st.markdown(f"- **confidence:** {conf.value}")

    kind = getattr(fig, "kind", None)
    if kind == "fact":
        # ResolvedFact provenance: the exact filing coordinates.
        for attr, lbl in (
            ("tag", "XBRL tag"),
            ("period_start", "period start"),
            ("period_end", "period end"),
            ("form", "form"),
            ("accession", "accession"),
            ("filed", "filed"),
        ):
            v = getattr(fig, attr, None)
            if v is not None:
                st.markdown(f"- **{lbl}:** `{v}`")
    elif kind == "metric":
        # ComputedMetric provenance: how the number was built.
        if getattr(fig, "status", None):
            st.markdown(f"- **status:** {fig.status}")
        if getattr(fig, "label", None):
            st.markdown(f"- **label:** {fig.label}")
        if getattr(fig, "formula", ""):
            st.markdown(f"- **formula:** `{fig.formula}`")
        if getattr(fig, "component_ids", None):
            st.markdown("- **built from:** " + ", ".join(f"`{c}`" for c in fig.component_ids))
        breakdown = getattr(fig, "breakdown", None) or []
        if breakdown:
            st.markdown("- **reconciliation:**")
            for row in breakdown:
                rv = "—" if row.value is None else f"{row.value:,.0f}"
                ref = f"`{row.figure_id}`" if row.figure_id else "—"
                st.markdown(f"    - {row.label}: {rv}  ({ref})")

    for note in getattr(fig, "notes", None) or []:
        st.markdown(f"- _note:_ {note}")


# ---------------------------------------------------------------------------
# Header + scorecard band
# ---------------------------------------------------------------------------

def render_header(brief: Brief) -> None:
    fin = brief.fin
    st.title(f"{fin.entity_name} ({brief.ticker})")

    c1, c2, c3 = st.columns([1, 2, 2])
    c1.metric("Anchor fiscal year", f"FY{brief.fiscal_year}")
    c2.markdown(
        f"**SIC {fin.sic or '—'}**  \n{fin.sic_description or 'industry n/a'}"
    )
    with c3:
        render_scorecard_band(brief)


def render_scorecard_band(brief: Brief) -> None:
    """Render the anchor-year credit band, colored by tier, with an auditable expander."""
    year = brief.fiscal_year
    band = brief.fin.figures.get(make_figure_id("credit_band", year))
    if band is None:
        st.markdown("**Credit band:** :gray[not computed]")
        return

    label = band.label or "unknown"
    color = _BAND_COLOR.get(label, "gray")
    st.markdown(f"**Credit band (FY{year}):** :{color}[**{label.upper()}**]")
    with st.expander("🔍 how this band was reached"):
        _render_provenance(band)


# ---------------------------------------------------------------------------
# Deterministic panels
# ---------------------------------------------------------------------------

def render_credit_panel(brief: Brief) -> None:
    year = brief.fiscal_year
    st.subheader("Credit & capital structure")
    render_figure(brief, "total_debt", year, "Total debt")
    render_figure(brief, "net_debt", year, "Net debt")
    render_figure(brief, "total_leverage", year, "Total leverage")
    render_figure(brief, "net_leverage", year, "Net leverage")
    render_figure(brief, "interest_coverage", year, "Interest coverage")
    render_figure(brief, "cash_interest_coverage", year, "Cash interest coverage")
    render_figure(brief, "fcf", year, "Free cash flow (OCF − capex)")
    render_figure(brief, "fcf_to_debt", year, "FCF / total debt")
    render_figure(brief, "liquidity", year, "Liquidity (cash + ST investments)")


def render_survival_panel(brief: Brief) -> None:
    year = brief.fiscal_year
    st.subheader("Survival")
    render_figure(brief, "deleveraging_trajectory", year, "Deleveraging trajectory")
    render_figure(brief, "coverage_durability", year, "Coverage durability")
    render_figure(brief, "liquidity_runway", year, "Liquidity runway")

    # Maturity wall: reconciled schedule if present, else explicit proxy degradation.
    wall = brief.fin.figures.get(make_figure_id("maturity_wall", year))
    render_figure(brief, "maturity_wall", year, "Maturity wall")
    if wall is not None and wall.label == "proxy":
        st.caption(
            "⚠ maturity wall degraded to the current-debt proxy — no reconciled "
            "footnote schedule was parsed for this filing."
        )
    elif wall is not None and wall.label == "schedule":
        st.caption("✓ maturity wall from a reconciled footnote schedule (see source).")


def render_ebitda_bridge(brief: Brief) -> None:
    """Render the EBITDA reconciliation from the ebitda metric's breakdown rows."""
    year = brief.fiscal_year
    st.subheader("EBITDA bridge")
    ebitda = brief.fin.figures.get(make_figure_id("ebitda", year))
    if ebitda is None or not getattr(ebitda, "breakdown", None):
        # No breakdown to walk — still show the figure (or its not-found reason).
        render_figure(brief, "ebitda", year, "EBITDA")
        if ebitda is not None and not ebitda.breakdown:
            st.caption("No reconciliation rows available for this year.")
        return

    st.markdown(f"**EBITDA (FY{year}):** {format_value(ebitda)}  {_TIER_BADGE.get(ebitda.confidence) or ''}")
    st.caption("Operating income → + D&A → = EBITDA. Each row expands to its source figure.")
    for row in ebitda.breakdown:
        rv = "—" if row.value is None else format_usd(row.value)
        st.markdown(f"**{row.label}:** {rv}")
        if row.figure_id:
            with st.expander(f"🔍 source — {row.label}"):
                comp = brief.fin.figures.get(row.figure_id)
                if comp is not None:
                    _render_provenance(comp)
                else:
                    st.markdown(f"- **figure_id:** `{row.figure_id}` (derived total; no standalone figure)")

    with st.expander("🔍 source — EBITDA metric"):
        _render_provenance(ebitda)


def render_covenant_panel(brief: Brief) -> None:
    year = brief.fiscal_year
    st.subheader("Covenant screen")
    st.caption("Illustrative screening bands, **not** real covenants (real covenants live in the credit agreement).")
    for concept, label in (
        ("covenant_leverage", "Leverage vs illustrative 4x / 6x bands"),
        ("covenant_coverage", "Coverage vs illustrative 2x / 3x floor"),
    ):
        fig = brief.fin.figures.get(make_figure_id(concept, year))
        flag = getattr(fig, "label", None) if fig is not None else None
        color = _FLAG_COLOR.get(flag, "gray")
        actual = format_value(fig) if fig is not None else "—"
        flag_txt = (flag or "unknown").upper()
        st.markdown(f"**{label}:** {actual} → :{color}[**{flag_txt}**]")
        if fig is not None:
            with st.expander(f"🔍 source — {concept}"):
                _render_provenance(fig)


def render_operating_panel(brief: Brief) -> None:
    year = brief.fiscal_year
    st.subheader("Operating")
    render_figure(brief, "revenue", year, "Revenue")
    render_figure(brief, "gross_margin", year, "Gross margin")
    render_figure(brief, "operating_margin", year, "Operating margin")
    render_figure(brief, "net_margin", year, "Net margin")
    render_figure(brief, "revenue_yoy", year, "Revenue YoY growth")
    render_figure(brief, "revenue_cagr", year, "Revenue CAGR (window)")
    render_figure(brief, "roe", year, "Return on equity")


# ---------------------------------------------------------------------------
# Degradation guard
# ---------------------------------------------------------------------------

def render_degraded_status(brief: Brief) -> None:
    """Render an explicit reason when the whole financials object is degraded."""
    fin = brief.fin
    st.error(
        f"No industrial credit brief available for {brief.ticker}: "
        f"status = `{fin.status}`."
    )
    if fin.status_detail:
        st.markdown(f"**Reason:** {fin.status_detail}")
