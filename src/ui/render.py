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
from src.llm.schemas.citations import Claim

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
            render_source(brief, figure_id)
        return None

    return render_figure_object(brief, fig, display_label)


def render_figure_by_id(brief: Brief, figure_id: str, label: str | None = None) -> object | None:
    """Same as render_figure but keyed directly by figure_id (used for bridge rows)."""
    fig = brief.fin.figures.get(figure_id)
    display_label = label or figure_id
    if fig is None:
        st.markdown(f"**{display_label}:** :red[not found — see filing]")
        with st.expander("🔍 source"):
            render_source(brief, figure_id)
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
        render_source(brief, fig.figure_id)
    return fig


def render_source(brief: Brief, figure_id: str) -> None:
    """The single provenance drill-down in the app.

    Given a figure_id, render its full, independently-computed source: XBRL tag /
    period / accession for a fact; formula / components / reconciliation / notes for
    a computed metric. A rendered number and an LLM claim's figure-citation open THIS
    identical body — there is exactly one provenance UX in the app.
    """
    fig = brief.fin.figures.get(figure_id)
    if fig is None:
        st.markdown(f"- **figure_id:** `{figure_id}`")
        st.markdown("- no independently-computed figure exists for this id")
        return
    _render_provenance(fig)


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


# ===========================================================================
# LLM panels (Phase 3, Step 3) — claim→source drill-down
#
# The credibility centerpiece: an AI-written claim's figure-citation opens the
# SAME render_source() drill-down as any rendered number, so a reader can always
# check the AI against the independently-computed figure underneath it.
# ===========================================================================

# Human labels for the 10-K sections a claim can be lifted from.
_SECTION_LABELS = {
    "item_1": "Item 1 — Business",
    "item_1a": "Item 1A — Risk Factors",
    "item_7": "Item 7 — MD&A",
    "debt_footnote": "Debt footnote (Item 8)",
}

# Panel self-reported status -> honest badge. "ok" gets no badge.
_PANEL_STATUS_BADGE = {
    "validation_failed": ":orange[**⚠ did not fully validate this run**]",
    "confidence_gap": ":orange[**⚠ confidence gap — a flagged figure was not caveated**]",
    "parse_error": ":orange[**⚠ output could not be parsed this run**]",
}

# Reasoning-panel verdict -> (display text, color).
_SYNTHESIS_VERDICT = {
    "can_service": ("CAN SERVICE", "green"),
    "conditional": ("CONDITIONAL", "orange"),
    "cannot_service": ("CANNOT SERVICE", "red"),
}
_ADDBACK_VERDICT = {
    "adjusted_fair": ("ADJUSTED EBITDA FAIR", "green"),
    "haircut_warranted": ("HAIRCUT WARRANTED", "orange"),
    "reject_adjustments": ("REJECT ADJUSTMENTS", "red"),
}


def _render_panel_status(status: str | None) -> None:
    """Render the honest per-panel status badge (nothing for a clean 'ok')."""
    badge = _PANEL_STATUS_BADGE.get(status or "")
    if badge:
        st.markdown(badge)


def _render_citation(brief: Brief, citation: object) -> None:
    """Render one citation: a figure opens render_source; a section quotes the filing."""
    if citation.kind == "figure":
        with st.expander(f"🔍 figure source — `{citation.ref}`"):
            render_source(brief, citation.ref)
    else:  # section
        section = _SECTION_LABELS.get(citation.ref, citation.ref)
        excerpt = (citation.excerpt or "").strip()
        if excerpt:
            st.markdown(f"↳ verbatim from **{section}**:")
            st.markdown(f"> {excerpt}")
        else:
            st.caption(f"↳ cited to {section} (no excerpt supplied)")


def render_claim(brief: Brief, claim: object, year: int) -> None:
    """The credibility centerpiece: a claim's text + each citation's drill-down.

    Figure citations open the identical render_source() panel as any rendered
    number; section citations quote the verbatim filing passage.
    """
    text = (getattr(claim, "text", "") or "").strip()
    st.markdown(text if text else "_(no content surfaced this run)_")
    for citation in getattr(claim, "citations", None) or []:
        _render_citation(brief, citation)


def _render_reasoning_claim(brief: Brief, label: str, text: str, citations: list, year: int) -> None:
    """Render a reasoning-panel prose clause as a claim.

    When the panel supplied figure citations, route the clause through render_claim
    (synthesizing a Claim from prose + the panel's figure citations) so the clause's
    figure-anchors are expandable via the same claim→source UX. Otherwise render the
    prose alone (an empty/validation shell keeps valid empty content, never a crash).
    """
    st.markdown(f"**{label}**")
    text = (text or "").strip()
    if citations:
        render_claim(brief, Claim(text=text or " ", citations=list(citations)), year)
    else:
        st.markdown(text if text else "_(no content surfaced this run)_")


def _render_caveats(caveats: list) -> None:
    caveats = [c for c in (caveats or []) if (c or "").strip()]
    if caveats:
        st.markdown("**Confidence caveats:**")
        for c in caveats:
            st.markdown(f"- {c}")


# --- Panel A: anchored synthesis (the money-shot, rendered first) -----------

def render_synthesis_panel(brief: Brief) -> None:
    env = brief.synthesis
    st.subheader("🧭 Panel A — Anchored credit synthesis")
    if env is None:
        st.info("Synthesis panel was not generated for this brief.")
        return
    panel = env.panel
    year = brief.fiscal_year

    verdict_txt, verdict_color = _SYNTHESIS_VERDICT.get(
        getattr(panel, "verdict", None), (str(getattr(panel, "verdict", "—")).upper(), "gray")
    )
    st.markdown(f"### Verdict: :{verdict_color}[**{verdict_txt}**]")
    _render_panel_status(env.validation.status)

    # Thesis is the load-bearing claim; its figure-anchors are expandable.
    _render_reasoning_claim(brief, "Thesis", getattr(panel, "thesis", ""), panel.citations, year)
    st.markdown(f"**Spine reading.** {(getattr(panel, 'spine_reading', '') or '').strip() or '—'}")
    st.markdown(f"**Swing factor.** {(getattr(panel, 'swing_factor', '') or '').strip() or '—'}")
    _render_caveats(getattr(panel, "confidence_caveats", []))


# --- Panel B: add-back adversary (bull vs skeptic) --------------------------

def render_addback_panel(brief: Brief) -> None:
    env = brief.addback_adversary
    st.subheader("⚖️ Panel B — Add-back adversary")
    if env is None:
        st.info("Add-back adversary panel was not generated for this brief.")
        return
    panel = env.panel
    year = brief.fiscal_year

    verdict_txt, verdict_color = _ADDBACK_VERDICT.get(
        getattr(panel, "verdict", None), (str(getattr(panel, "verdict", "—")).upper(), "gray")
    )
    st.markdown(f"### Verdict: :{verdict_color}[**{verdict_txt}**]")
    _render_panel_status(env.validation.status)

    st.markdown(f"{(getattr(panel, 'headline', '') or '').strip() or '_(no headline this run)_'}")

    # Bull vs skeptic — each side cited via render_claim over the add-back figures.
    _render_reasoning_claim(brief, "🟢 Accept case (bull)", getattr(panel, "accept_case", ""), panel.citations, year)
    _render_reasoning_claim(brief, "🔴 Challenge case (skeptic)", getattr(panel, "challenge_case", ""), panel.citations, year)
    st.markdown(f"**Leverage read.** {(getattr(panel, 'leverage_read', '') or '').strip() or '—'}")
    st.markdown(f"**Excluded candidates.** {(getattr(panel, 'excluded_candidate_read', '') or '').strip() or '—'}")
    _render_caveats(getattr(panel, "confidence_caveats", []))


# --- Descriptive panels -----------------------------------------------------

def _render_claim_list(brief: Brief, claims: list, year: int, empty_msg: str) -> None:
    claims = list(claims or [])
    real = [c for c in claims if (getattr(c, "text", "") or "").strip()]
    if not real:
        st.markdown(f"_{empty_msg}_")
        return
    for claim in real:
        render_claim(brief, claim, year)


def render_business_summary_panel(brief: Brief) -> None:
    env = brief.business_summary
    st.subheader("🏢 Business summary")
    if env is None:
        st.info("Business summary was not generated for this brief.")
        return
    panel, year = env.panel, brief.fiscal_year
    _render_panel_status(env.validation.status)
    headline = getattr(panel, "headline", None)
    if headline is not None and (getattr(headline, "text", "") or "").strip():
        render_claim(brief, headline, year)
    else:
        st.markdown("_(no headline surfaced this run)_")
    st.markdown("**Business lines**")
    _render_claim_list(brief, getattr(panel, "business_lines", []), year, "No segments surfaced this run.")


def render_risks_panel(brief: Brief) -> None:
    env = brief.risks
    st.subheader("⚠️ Company-specific risks")
    if env is None:
        st.info("Risks panel was not generated for this brief.")
        return
    panel, year = env.panel, brief.fiscal_year
    _render_panel_status(env.validation.status)
    _render_claim_list(
        brief, getattr(panel, "company_specific_risks", []), year,
        "No company-specific risks surfaced this run.",
    )
    note = (getattr(panel, "boilerplate_note", None) or "").strip()
    if note:
        st.caption(f"Boilerplate note: {note}")


def render_revenue_drivers_panel(brief: Brief) -> None:
    env = brief.revenue_drivers
    st.subheader("📈 Revenue drivers")
    if env is None:
        st.info("Revenue drivers panel was not generated for this brief.")
        return
    panel, year = env.panel, brief.fiscal_year
    _render_panel_status(env.validation.status)
    st.markdown("**Drivers**")
    _render_claim_list(brief, getattr(panel, "drivers", []), year, "No drivers surfaced this run.")
    st.markdown("**Segment commentary**")
    _render_claim_list(brief, getattr(panel, "segment_commentary", []), year, "No segment commentary this run.")


def render_qoe_candidates_panel(brief: Brief) -> None:
    env = brief.qoe_candidates
    st.subheader("🔎 Quality-of-earnings candidates")
    if env is None:
        st.info("QoE candidates panel was not generated for this brief.")
        return
    panel, year = env.panel, brief.fiscal_year
    _render_panel_status(env.validation.status)
    _render_claim_list(
        brief, getattr(panel, "claimed_one_time_items", []), year,
        "No candidates surfaced this run.",
    )
