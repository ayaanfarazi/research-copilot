"""
Deterministic-layer renderers for the credit brief (Phase 3, Step 2).

The credibility money-shot lives here: `render_figure` shows a value and an
expand-to-source drill-down that reads as a provenance story — a raw fact shows its
reported value, XBRL tag, period, filing and a live SEC.gov link; a computed metric
shows a plain-English recipe and lets the reader trace each input one level deeper
until it bottoms out at raw facts. EVERY number the app prints goes through the
single shared `render_source`, so nothing is ever an un-sourced float, and the same
helper carries LLM claim→source. figure_ids, raw floats, and enum internals appear
only behind an off-by-default "technical details" toggle.

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
from src.ui.format import (
    confidence_phrase,
    fmt_date,
    fmt_money,
    fmt_value,
    label_for,
    sec_filing_url,
)

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
#
# The canonical formatters now live in src.ui.format (pure, unit-tested). These
# thin aliases keep the historical call sites (_headline, the EBITDA bridge)
# working while guaranteeing every printed number is rounded and unit-aware.
# ---------------------------------------------------------------------------

def format_usd(v: float | None) -> str:
    return fmt_money(v)


def format_value(fig: object) -> str:
    """Human value for a figure, unit- and status-aware (delegates to fmt_value)."""
    return fmt_value(fig)


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


# ===========================================================================
# The single provenance drill-down — provenance as a story a reader can act on
#
# One shared helper, called by every rendered number and every AI claim's figure
# citation, so there is exactly ONE provenance UX to trust and audit. It reads
# top-to-bottom as: plain value -> plain-English recipe -> trace each input one
# level deeper -> bottom out at a raw fact with its XBRL tag, period, filing, and
# a live SEC.gov link. figure_ids, raw floats, and enum internals live ONLY behind
# an off-by-default "technical details" toggle — never in the default view.
#
# Streamlit constraint: st.expander cannot nest. The top-level "source" affordance
# may be an expander (owned by the caller), but every deeper trace is a
# session_state-keyed chip button that toggles an inline block. A metric input
# expands to just its own recipe + chips with a light left-border cue (never a heavy
# box); only a raw-fact leaf gets a bordered card. Chips are laid out horizontally
# via st.columns placed directly inside a container/expander (never inside another
# column) so the drill-down can recurse arbitrarily deep without nesting columns.
# ===========================================================================

# Formula operator -> the glyph a person reads. Operators are matched as
# whitespace-delimited tokens so hyphens inside concept words are never mistaken
# for subtraction (e.g. the credit_band formula's "weakest-link").
_OP_GLYPH = {"/": "÷", "*": "×", "×": "×", "+": "+", "-": "−", "−": "−"}

# Per-run registry so a widget key stays STABLE across reruns (required for a
# click to re-bind and toggle to work) while still being unique when the same
# drill-down path is rendered twice in one run (a rendered figure and an AI claim
# can both cite it). `_begin_run()` clears the registry at the top of each script
# run; the Nth appearance of a path in a run deterministically gets suffix N, which
# is identical on the next run, so open/closed state survives reruns.
_KEY_COUNTS: dict[str, int] = {}


def begin_render_run() -> None:
    """Reset the per-run widget-key registry. Call ONCE at the top of each script run
    (app.py and any AppTest harness) so drill-down widget keys stay stable."""
    _KEY_COUNTS.clear()


def _stable_key(base: str) -> str:
    n = _KEY_COUNTS.get(base, 0)
    _KEY_COUNTS[base] = n + 1
    return base if n == 0 else f"{base}#{n}"


def _toggle(label: str, state_key: str) -> bool:
    """A button that flips a session_state flag; returns whether it's open.

    The flag key (`state_key`) is stable per drill-down path so the open/closed
    state persists; the button widget key is disambiguated per run so two renders
    of the same path in one run don't collide.
    """
    widget_key = _stable_key(f"btn::{state_key}")
    if st.button(label, key=widget_key):
        st.session_state[state_key] = not st.session_state.get(state_key, False)
    return bool(st.session_state.get(state_key, False))


def _formula_ops(formula: str) -> list[str]:
    """Operator glyphs in order, from whitespace-delimited operator tokens only."""
    return [_OP_GLYPH[t] for t in (formula or "").split() if t in _OP_GLYPH]


def _lower_first(label: str) -> str:
    """Lower-case a label's first letter for mid-sentence use, but keep acronyms
    (EBITDA, FCF) upper-case. "Interest expense" -> "interest expense"; "EBITDA"
    stays "EBITDA"; "adjusted EBITDA" stays "adjusted EBITDA"."""
    if not label:
        return label
    head = label.split(" ", 1)[0]
    if head.isupper():
        return label
    return label[:1].lower() + label[1:]


def _recipe_ops(fig: object, n_terms: int) -> list[str] | None:
    """The operator glyphs joining `n_terms` components, or None if they don't align.

    Prefers the breakdown row prefixes (a bridge like EBITDA is all "+"), since a
    metric's free-text formula can name collapsed terms (e.g. "operating_income + D&A"
    for three components). Falls back to the formula's own operator tokens.
    """
    breakdown = getattr(fig, "breakdown", None) or []
    comp_rows = [r for r in breakdown if not r.label.strip().startswith("=")]
    if n_terms >= 2 and len(comp_rows) == n_terms:
        return [_OP_GLYPH.get(r.label.strip()[:1], "+") for r in comp_rows[1:]]
    ops = _formula_ops(getattr(fig, "formula", "") or "")
    return ops if len(ops) == n_terms - 1 else None


def _recipe_line(brief: Brief, fig: object) -> str:
    """One clean sentence: plain-label formula and value formula as TWO parallel
    expressions, never interleaved per term.

    "Computed as EBITDA ÷ interest expense = $129.4B ÷ $2,935M = 44.1×."
    """
    comps: list[tuple[str, str]] = []
    for cid in getattr(fig, "component_ids", None) or []:
        comp = brief.fin.figures.get(cid)
        concept = cid.split(":")[0]
        value = fmt_value(comp) if comp is not None else "not found"
        comps.append((label_for(concept), value))

    result = fmt_value(fig)
    if not comps:
        return f"Reported as {result}."

    ops = _recipe_ops(fig, len(comps))
    if ops is not None:
        label_expr = _lower_first(comps[0][0])
        value_expr = comps[0][1]
        for op, (lbl, val) in zip(ops, comps[1:]):
            label_expr += f" {op} {_lower_first(lbl)}"
            value_expr += f" {op} {val}"
        return f"Computed as {label_expr} = {value_expr} = {result}."

    # No clean operator alignment: parallel lists (still labels-then-values, never
    # interleaved) so a reader never sees "Net debt Net cash ÷ Adjusted Ebitda".
    labels = ", ".join(_lower_first(lbl) for lbl, _ in comps)
    values = ", ".join(val for _, val in comps)
    return f"Computed from {labels} = {values} = {result}."


def render_source(brief: Brief, figure_id: str) -> None:
    """The single provenance drill-down in the app.

    Given a figure_id, tell its source story: a fact shows its reported value, XBRL
    tag, period, filing and a SEC.gov link; a computed metric shows a plain-English
    recipe and lets the reader trace each input one level deeper (progressive
    disclosure) until it bottoms out at raw facts. A rendered number and an AI
    claim's figure-citation open THIS identical body.
    """
    fig = brief.fin.figures.get(figure_id)
    if fig is None:
        st.markdown("_No independently-computed figure exists for this reference._")
        return
    _render_source_body(brief, fig, path=figure_id, depth=0)


def _render_source_body(brief: Brief, fig: object, path: str, depth: int = 0) -> None:
    kind = getattr(fig, "kind", None)
    if kind == "metric":
        # A metric shows recipe + chips only; its raw internals never surface here.
        _render_metric_source(brief, fig, path, depth)
    else:
        _render_fact_source(brief, fig)
        _render_technical_details(fig, path)


def _render_fact_source(brief: Brief, fig: object) -> None:
    """A raw fact: reported value, XBRL tag, human period, filing, confidence, link."""
    st.markdown(f"**Reported value:** {fmt_value(fig)}")

    tag = getattr(fig, "tag", None)
    if tag:
        st.markdown(f"**XBRL tag:** `{tag}`")

    period_end = getattr(fig, "period_end", None)
    period_start = getattr(fig, "period_start", None)
    if period_start and period_end:
        st.markdown(f"**Period:** {fmt_date(period_start)} – {fmt_date(period_end)}")
    elif period_end:
        st.markdown(f"**Period:** as of {fmt_date(period_end)}")

    form = getattr(fig, "form", None)
    filed = getattr(fig, "filed", None)
    if form or filed:
        st.markdown(f"**Filing:** {form or '—'}, filed {fmt_date(filed)}")

    phrase = confidence_phrase(getattr(fig, "confidence", None))
    if phrase:
        st.markdown(f"**Confidence:** {phrase}")

    url = sec_filing_url(brief.fin.cik, getattr(fig, "accession", None))
    if url:
        st.markdown(f"**[↗ Open this filing on SEC.gov]({url})**")
    else:
        st.caption(
            "Filing reference unavailable — no accession on this figure; "
            "check the issuer's filings directly on SEC.gov EDGAR."
        )


def _chip_weights(n: int) -> list[int]:
    """Column weights for `n` compact, left-aligned trace chips plus a trailing spacer
    so they stay narrow instead of stretching full width."""
    return [3] * n + [max(1, 12 - 3 * n)]


def _render_metric_source(brief: Brief, fig: object, path: str, depth: int = 0) -> None:
    """A computed metric: the recipe sentence, then compact horizontal trace chips.

    No "How it's computed" / "Trace each input" headers. A metric input expands to
    just its own recipe + chips (light left-border cue via a blockquote, no box); a
    fact input expands to the bordered leaf card.
    """
    recipe = _recipe_line(brief, fig)
    st.markdown(recipe if depth == 0 else f"> {recipe}")

    components = getattr(fig, "component_ids", None) or []
    if not components:
        return

    # Horizontal, compact trace chips (one per component input).
    cols = st.columns(_chip_weights(len(components)))
    states: list[tuple[str, object, bool]] = []
    for i, cid in enumerate(components):
        comp = brief.fin.figures.get(cid)
        concept = cid.split(":")[0]
        lbl = label_for(concept)
        val = fmt_value(comp) if comp is not None else "not found"
        state_key = f"trace::{path}>{cid}"
        opened = bool(st.session_state.get(state_key, False))
        chip = f"{lbl} {val}  ▾" if opened else f"{lbl} {val}  ▸ trace"
        with cols[i]:
            if st.button(chip, key=_stable_key(f"btn::{state_key}")):
                opened = not opened
                st.session_state[state_key] = opened
        states.append((cid, comp, opened))

    # Expansions render below the chip row (never inside a chip's column), so the
    # recursion stays a flat sequence of column blocks — no nested columns.
    for cid, comp, opened in states:
        if not opened:
            continue
        child_path = f"{path}>{cid}"
        if comp is None:
            st.markdown("> _derived within this metric — no standalone figure._")
        elif getattr(comp, "kind", None) == "metric":
            _render_metric_source(brief, comp, child_path, depth + 1)
        else:
            with st.container(border=True):
                _render_source_body(brief, comp, path=child_path, depth=depth + 1)


def _render_technical_details(fig: object, path: str) -> None:
    """Off-by-default toggle exposing figure_id, the raw value, and enum internals.

    Kept for debugging/audit; deliberately NOT part of the default provenance view
    so the reader never sees a figure_id, a raw float, or an enum name unprompted.
    """
    if not _toggle("⚙ technical details", f"tech::{path}"):
        return
    with st.container(border=True):
        st.markdown(f"- `figure_id` = `{getattr(fig, 'figure_id', '—')}`")
        val = getattr(fig, "value", None)
        unit = getattr(fig, "unit", None) or ""
        st.markdown(f"- raw `value` = `{val!r}` {unit}".rstrip())
        conf = getattr(fig, "confidence", None)
        if conf is not None:
            st.markdown(f"- `confidence` = `{getattr(conf, 'value', conf)}`")
        if getattr(fig, "status", None):
            st.markdown(f"- `status` = `{fig.status}`")
        if getattr(fig, "formula", ""):
            st.markdown(f"- `formula` = `{fig.formula}`")
        if getattr(fig, "component_ids", None):
            st.markdown("- `component_ids` = " + ", ".join(f"`{c}`" for c in fig.component_ids))
        for note in getattr(fig, "notes", None) or []:
            st.markdown(f"- _note:_ {note}")


# ---------------------------------------------------------------------------
# Header + scorecard band
# ---------------------------------------------------------------------------

def render_header(brief: Brief) -> None:
    """Title + issuer identity. The scorecard band is its own orderable section
    (render_scorecard_band) so the view toggle can place it per view."""
    fin = brief.fin
    st.title(f"{fin.entity_name} ({brief.ticker})")

    c1, c2 = st.columns([1, 3])
    c1.metric("Anchor fiscal year", f"FY{brief.fiscal_year}")
    c2.markdown(
        f"**SIC {fin.sic or '—'}**  \n{fin.sic_description or 'industry n/a'}"
    )


def render_scorecard_band(brief: Brief) -> None:
    """Render the anchor-year credit band, colored by tier, with an auditable expander."""
    year = brief.fiscal_year
    st.subheader("🏦 Credit scorecard")
    band = brief.fin.figures.get(make_figure_id("credit_band", year))
    if band is None:
        st.markdown("**Credit band:** :gray[not computed]")
        return

    label = band.label or "unknown"
    color = _BAND_COLOR.get(label, "gray")
    st.markdown(f"**Credit band (FY{year}):** :{color}[**{label.upper()}**]")
    with st.expander("🔍 how this band was reached"):
        render_source(brief, band.figure_id)


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
                render_source(brief, row.figure_id)

    with st.expander("🔍 source — EBITDA metric"):
        render_source(brief, ebitda.figure_id)


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
            with st.expander(f"🔍 source — {label_for(concept)}"):
                render_source(brief, fig.figure_id)


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
        concept = (citation.ref or "").split(":")[0]
        with st.expander(f"🔍 source — {label_for(concept)}"):
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
