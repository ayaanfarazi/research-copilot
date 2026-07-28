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
from src.ui.format import (
    confidence_phrase,
    fmt_date,
    fmt_money,
    fmt_value,
    humanize_refs,
    label_for,
    phrase_label,
    sec_filing_url,
    titlecase_entity,
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

# Semantic tokens -> presentation. One vocabulary for both the banner (a colored
# callout box) and the tiles (colored tier words), so a band and its dimensions
# read as one system. success=green, neutral=gray, warning=amber, danger=red.
_TOKEN_COLOR = {"success": "green", "neutral": "gray", "warning": "orange", "danger": "red"}

# Credit-band label -> semantic token. adequate/moderate are neutral (not a
# verdict either way); only strong is success, only distressed is danger.
_BAND_TOKEN = {
    "strong": "success",
    "adequate": "neutral",
    "moderate": "neutral",
    "stretched": "warning",
    "distressed": "danger",
}


# ---------------------------------------------------------------------------
# Value formatting
#
# The canonical formatters now live in src.ui.format (pure, unit-tested). This
# thin alias keeps the historical call site (_headline) working while guaranteeing
# every printed number is rounded and unit-aware.
# ---------------------------------------------------------------------------

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
    """Page hero: title-cased entity name + a muted provenance subline.

    The scorecard band is its own orderable section (render_scorecard_band) so the
    view toggle can place it per view.
    """
    fin = brief.fin
    st.title(titlecase_entity(fin.entity_name))
    st.caption(
        f"{brief.ticker} · fiscal year {brief.fiscal_year} · figures from SEC filings"
    )
    if fin.sic:
        st.caption(f":gray[SIC {fin.sic} · {fin.sic_description or 'industry n/a'}]")


# The four scorecard dimensions, in the order the mockup shows them.
_SCORECARD_DIMS = ("leverage", "coverage", "trajectory", "liquidity")
_DIM_NAME = {
    "leverage": "Leverage",
    "coverage": "Coverage",
    "trajectory": "Trajectory",
    "liquidity": "Liquidity",
}

# Underlying-trend labels for the trajectory dimension (deleveraging_trajectory).
_TRAJ_SEVERITY = {"improving": 0, "flat": 1, "worsening": 2}


def _sev_token(sev: float | int | None) -> str:
    """Severity 0-3 -> semantic token. Missing severity is neutral, never a verdict."""
    if sev is None:
        return "neutral"
    return {0: "success", 1: "neutral", 2: "warning", 3: "danger"}.get(int(sev), "neutral")


def _cap(word: str) -> str:
    return word[:1].upper() + word[1:] if word else word


def _band_is_withheld(band: object) -> bool:
    """True when no credit verdict is presented (degraded / issuer type doesn't fit)."""
    label = getattr(band, "label", None) or ""
    return (
        getattr(band, "status", None) == "not_found"
        or label.startswith("not_applicable")
        or label == "not_assessable"
    )


def _withheld_reason(band: object) -> str:
    """A plain sentence for why the credit framing is withheld, pulled from notes."""
    label = getattr(band, "label", None) or ""
    notes = getattr(band, "notes", None) or []
    if label.startswith("not_applicable"):
        return notes[0] if notes else "The industrial credit framing does not apply to this issuer type."
    if label == "not_assessable":
        return (
            "Both leverage and coverage are missing for this filing, so a credit "
            "band can't be assessed."
        )
    return notes[0] if notes else "A credit band is not available for this issuer."


# --- Banner summary clauses (derived from the spine dimensions) ---------------
_LEV_CLAUSE = {
    "net cash": "net cash balance sheet", "strong": "low leverage",
    "adequate": "moderate leverage", "stretched": "elevated leverage",
    "distressed": "very high leverage", "earnings negative": "negative earnings",
}
_COV_CLAUSE = {
    "strong": "very high interest coverage", "adequate": "solid interest coverage",
    "stretched": "thin interest coverage", "distressed": "weak interest coverage",
    "earnings negative": "negative earnings",
}
_LIQ_SUMMARY = {
    "comfortable": "no near-term servicing risk", "adequate": "adequate near-term liquidity",
    "tight": "tight near-term liquidity", "acute": "acute liquidity pressure",
}
_BAND_SUMMARY_FALLBACK = {
    "strong": "Comfortable credit profile.", "adequate": "Adequate credit profile.",
    "stretched": "Stretched credit profile.", "distressed": "Distressed credit profile.",
}


def _score_label(brief: Brief, dim: str, year: int) -> str | None:
    fig = brief.fin.figures.get(make_figure_id(f"score_{dim}", year))
    return getattr(fig, "label", None) if fig is not None else None


def _band_summary(brief: Brief, band: object, year: int) -> str:
    """A one-line plain summary composed from the leverage / coverage / liquidity
    tiers (with a band-label fallback), matching the mockup's summary line."""
    clauses = [
        _LEV_CLAUSE.get(_score_label(brief, "leverage", year) or ""),
        _COV_CLAUSE.get(_score_label(brief, "coverage", year) or ""),
        _LIQ_SUMMARY.get(_score_label(brief, "liquidity", year) or ""),
    ]
    clauses = [c for c in clauses if c]
    if clauses:
        sentence = ", ".join(clauses)
        return _cap(sentence) + "."
    return _BAND_SUMMARY_FALLBACK.get(band.label or "", "Credit profile summary unavailable.")


def _rule_line(band: object) -> str:
    """One muted plain-English sentence for how the band is set, read from the band's
    own rule note / formula so it stays honest if the mechanism changes."""
    rule_note = ""
    for n in getattr(band, "notes", None) or []:
        if n.strip().lower().startswith("rule:"):
            rule_note = n
            break
    hay = f"{rule_note} {getattr(band, 'formula', '') or ''}".lower()

    clauses: list[str] = []
    if "weakest" in hay:
        clauses.append("the weakest of leverage and coverage sets the band")
    if "downgrade" in hay:
        clauses.append("a worsening trajectory can only downgrade it")
    if "flag" in hay:
        clauses.append("liquidity is flagged but never moves it")

    if clauses:
        return "Rule: " + ", ".join(clauses) + "."
    if rule_note:
        return "Rule: " + rule_note.split(":", 1)[1].strip()
    return "Rule: the weakest measured dimension sets the band."


def _dimension_tier(brief: Brief, dim: str, year: int) -> tuple[str, int | None]:
    """The tier word + severity to show for a dimension tile.

    Leverage / coverage / liquidity use their score_* tier. Trajectory shows the real
    YoY leverage trend (improving/flat/worsening) — that's what a reader means by
    "trajectory" — since the score collapses to "net cash (trajectory not binding)"
    for net-cash issuers; whether it actually binds is explained by the rule line.
    """
    score = brief.fin.figures.get(make_figure_id(f"score_{dim}", year))
    if score is None or score.value is None:
        return "no data", None
    sev = int(score.value)
    label = score.label or "no data"

    if dim == "trajectory":
        traj = brief.fin.figures.get(make_figure_id("deleveraging_trajectory", year))
        tl = getattr(traj, "label", None)
        if tl in _TRAJ_SEVERITY:
            return tl, _TRAJ_SEVERITY[tl]

    return label.split(" (")[0].strip(), sev


# --- Tile captions (derived from the key underlying figure where cheap) -------
_TRAJ_CAPTION = {
    "improving": "leverage fell YoY", "flat": "leverage flat YoY",
    "worsening": "leverage rose YoY",
}
_LIQ_CAPTION = {
    "comfortable": "cash covers near-term debt", "adequate": "adequate near-term cover",
    "tight": "tight near-term cover", "acute": "thin near-term cover",
}
_CAPTION_FALLBACK = {
    "leverage": "leverage vs. earnings", "coverage": "earnings vs. interest",
    "trajectory": "leverage trend YoY", "liquidity": "near-term liquidity",
}


def _dimension_caption(brief: Brief, dim: str, year: int, word: str) -> str:
    if dim == "coverage":
        ic = brief.fin.figures.get(make_figure_id("interest_coverage", year))
        v = getattr(ic, "value", None)
        if v is not None and getattr(ic, "status", None) not in ("not_meaningful", "not_found"):
            n = f"{v:.0f}" if v >= 10 else f"{v:.1f}"
            return f"earnings cover interest {n}×"
    if dim == "leverage":
        nl = brief.fin.figures.get(make_figure_id("net_leverage", year))
        if getattr(nl, "status", None) == "net_cash":
            return "net cash vs. earnings"
        v = getattr(nl, "value", None)
        if v is not None:
            return f"net debt ≈ {v:.1f}× earnings"
    if dim == "trajectory":
        return _TRAJ_CAPTION.get(word, _CAPTION_FALLBACK["trajectory"])
    if dim == "liquidity":
        return _LIQ_CAPTION.get(word, _CAPTION_FALLBACK["liquidity"])
    return _CAPTION_FALLBACK[dim]


def _render_scorecard_tiles(brief: Brief, year: int) -> None:
    """A responsive row of four dimension tiles (four across; stacks on narrow width)."""
    cols = st.columns(len(_SCORECARD_DIMS))
    for col, dim in zip(cols, _SCORECARD_DIMS):
        word, sev = _dimension_tier(brief, dim, year)
        color = _TOKEN_COLOR[_sev_token(sev)]
        caption = _dimension_caption(brief, dim, year, word)
        with col:
            st.markdown(f":gray[{_DIM_NAME[dim]}]")
            st.markdown(f"### :{color}[{_cap(word)}]")
            st.caption(caption)


def render_scorecard_band(brief: Brief) -> None:
    """Credit-standing banner + scorecard-as-tiles + the plain-English rule line.

    The banner is a semantic callout coloured by band (success/neutral/warning/
    danger); a withheld band renders a neutral informational banner that says why —
    never a colored verdict, never blank. Every tier still traces via render_source.
    """
    year = brief.fiscal_year
    band = brief.fin.figures.get(make_figure_id("credit_band", year))

    if band is None:
        st.info(
            "**Credit standing: not available** — the scorecard was not computed "
            "for this filing.",
            icon="ℹ️",
        )
        return

    # Degraded: issuer type / data doesn't fit the industrial credit framing.
    if _band_is_withheld(band):
        st.info(f"**Credit standing: not assessed**  \n{_withheld_reason(band)}", icon="ℹ️")
        with st.expander("🔍 source"):
            render_source(brief, band.figure_id)
        return

    label = band.label or "unknown"
    token = _BAND_TOKEN.get(label, "neutral")
    callout = _CALLOUT[token]
    callout(f"**Credit standing: {label}**  \n{_band_summary(brief, band, year)}", icon="🛡️")

    st.subheader(f"Why {label} — the scorecard")
    _render_scorecard_tiles(brief, year)
    st.caption(_rule_line(band))

    with st.expander("🔍 source"):
        render_source(brief, band.figure_id)


# ---------------------------------------------------------------------------
# Deterministic panels
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Reusable metric row — label + descriptor on the left, value + source pill on
# the right, and an inline drill-down that toggles directly below the row (the
# same shared provenance body, never wrapped in a second expander).
# ---------------------------------------------------------------------------

def _value_markdown(fig: object) -> str:
    """The row's right-hand value, bolded and status-coloured where meaningful."""
    if fig is None:
        return ":red[not found — see filing]"
    val = fmt_value(fig)
    status = getattr(fig, "status", None)
    if status == "net_cash":
        return f":green[**{val}**]"
    if status == "not_found" or val.startswith("not found"):
        return f":red[{val}]"
    if status in ("not_meaningful", "anomaly"):
        return f":orange[**{val}**]"
    return f"**{val}**"


# Categorical survival tiers -> semantic token (colored by meaning, not severity #).
_TIER_TOKEN = {
    "improving": "success", "strengthening": "success", "comfortable": "success",
    "flat": "neutral", "stable": "neutral", "adequate": "neutral",
    "worsening": "warning", "weakening": "warning", "tight": "warning",
    "acute": "danger", "critical": "danger",
}

# Covenant screen flag -> token + plain-English pass/watch/breach word.
_FLAG_TOKEN = {"green": "success", "amber": "warning", "red": "danger", "unknown": "neutral"}
_FLAG_WORD = {
    "green": "within band", "amber": "near band", "red": "outside band", "unknown": "no band",
}


def _tier_value_markdown(fig: object) -> str:
    """Right-hand value for a categorical survival metric: the plain tier word,
    coloured by meaning, with the supporting figure as muted secondary text."""
    if fig is None:
        return ":red[not found — see filing]"
    label = getattr(fig, "label", None)
    if not label:
        return _value_markdown(fig)
    color = _TOKEN_COLOR[_TIER_TOKEN.get(label, "neutral")]
    parts = [f":{color}[**{_cap(label)}**]"]
    if getattr(fig, "value", None) is not None:
        parts.append(f":gray[{fmt_value(fig)}]")
    return "  ".join(parts)


def _covenant_value_markdown(fig: object) -> str:
    """Right-hand value for a covenant screen row: the actual ratio + a green/amber/
    red status word vs. the illustrative band."""
    if fig is None:
        return ":red[not found — see filing]"
    flag = getattr(fig, "label", None) or "unknown"
    color = _TOKEN_COLOR[_FLAG_TOKEN.get(flag, "neutral")]
    return f"**{fmt_value(fig)}**  :{color}[{_FLAG_WORD.get(flag, flag)}]"


def _metric_row(
    brief: Brief,
    figure_id: str,
    label: str,
    descriptor: str = "",
    value_md: str | None = None,
) -> None:
    """The shared row primitive: plain label + optional one-line descriptor, a
    right-hand value, and a compact "🔍 source" pill that toggles the recipe/chips
    drill-down inline below the row (the shared source body — no nested expander).

    `value_md` overrides the default status-coloured value (used for categorical
    tiers and covenant flags); when omitted the figure's fmt_value is shown.
    """
    fig = brief.fin.figures.get(figure_id)
    state_key = f"rowsrc::{figure_id}"

    lc, vc, pc = st.columns([6, 2, 1])
    lc.markdown(f"**{label}**")
    if descriptor:
        lc.caption(descriptor)
    vc.markdown(value_md if value_md is not None else _value_markdown(fig))

    opened = bool(st.session_state.get(state_key, False))
    if pc.button("🔍 source", key=_stable_key(f"btn::{state_key}")):
        opened = not opened
        st.session_state[state_key] = opened

    if opened:
        # Inline, full-width provenance body (recipe + chips), same as everywhere else.
        render_source(brief, figure_id)

    st.divider()  # hairline separator between rows


def render_metric_row(
    brief: Brief, concept: str, year: int, label: str, descriptor: str,
    value_md: str | None = None,
) -> None:
    """One key-metric row keyed by concept+year (see _metric_row)."""
    _metric_row(brief, make_figure_id(concept, year), label, descriptor, value_md)


# One-line plain descriptors for the key credit metrics (mockup captions).
_KEY_CREDIT_METRICS = [
    ("net_leverage", "Net leverage", "how many years of earnings the net debt equals"),
    ("total_leverage", "Total leverage", "how many years of earnings the total debt equals"),
    ("interest_coverage", "Interest coverage", "how many times earnings cover the interest bill"),
    ("cash_interest_coverage", "Cash interest coverage", "how many times cash earnings cover cash interest"),
    ("fcf", "Free cash flow", "cash left after capital spending"),
    ("fcf_to_debt", "FCF / total debt", "free cash flow as a share of total debt"),
    ("liquidity", "Liquidity", "cash plus short-term investments"),
    ("total_debt", "Total debt", "all borrowings on the balance sheet"),
    ("net_debt", "Net debt", "debt minus cash and short-term investments"),
]


def render_credit_panel(brief: Brief) -> None:
    """The 'Key credit metrics' block — each metric as a row with an inline source."""
    year = brief.fiscal_year
    st.subheader("Key credit metrics")
    for concept, label, descriptor in _KEY_CREDIT_METRICS:
        render_metric_row(brief, concept, year, label, descriptor)


# Survival categorical rows: (concept, label, one-line descriptor).
_SURVIVAL_METRICS = [
    ("deleveraging_trajectory", "Deleveraging trajectory", "is leverage improving or worsening YoY"),
    ("coverage_durability", "Coverage durability", "how durable interest coverage looks"),
    ("liquidity_runway", "Liquidity runway", "how long cash covers near-term needs"),
]


def render_survival_panel(brief: Brief) -> None:
    year = brief.fiscal_year
    st.subheader("Survival")
    for concept, label, descriptor in _SURVIVAL_METRICS:
        fig = brief.fin.figures.get(make_figure_id(concept, year))
        render_metric_row(
            brief, concept, year, label, descriptor,
            value_md=_tier_value_markdown(fig),
        )

    # Maturity wall: a reconciled schedule renders as a figure; the degraded "proxy"
    # state renders as a plain-English amber callout (not "proxy ⚠ check source").
    wall = brief.fin.figures.get(make_figure_id("maturity_wall", year))
    if wall is not None and wall.label == "proxy":
        st.warning(
            "**Maturity wall — limited detail.** A year-by-year repayment schedule "
            "couldn't be parsed from this filing's debt footnote, so this shows total "
            "debt split into due-within-a-year vs. later. To see the exact schedule, "
            "open the debt footnote in the 10-K.",
            icon="⚠️",
        )
    else:
        render_figure(brief, "maturity_wall", year, "Maturity wall")
        if wall is not None and wall.label == "schedule":
            st.caption("✓ maturity wall from a reconciled footnote schedule (see source).")


def render_ebitda_bridge(brief: Brief) -> None:
    """Render the EBITDA reconciliation as clean rows (operating income → + D&A →
    = EBITDA). Each row's source pill reads as a plain label, never a snake_case
    breakdown token."""
    year = brief.fiscal_year
    st.subheader("EBITDA bridge")
    ebitda = brief.fin.figures.get(make_figure_id("ebitda", year))
    if ebitda is None or not getattr(ebitda, "breakdown", None):
        # No breakdown to walk — still show the figure (or its not-found reason).
        render_metric_row(brief, "ebitda", year, "EBITDA", "operating income + D&A")
        if ebitda is not None and not ebitda.breakdown:
            st.caption("No reconciliation rows available for this year.")
        return

    st.caption("Operating income + depreciation + amortization = EBITDA.")
    for row in ebitda.breakdown:
        value_md = f"**{fmt_money(row.value)}**"
        if row.figure_id:
            # Component fact/metric row: label plainly via label_for (never "+ foo").
            concept = row.figure_id.split(":")[0]
            _metric_row(brief, row.figure_id, label_for(concept), value_md=value_md)
        else:
            # The "= EBITDA" total row is the ebitda metric itself; source -> its recipe.
            _metric_row(
                brief, ebitda.figure_id, "EBITDA",
                "operating income + depreciation + amortization", value_md=value_md,
            )


# Covenant screen rows: (concept, label, descriptor).
_COVENANT_METRICS = [
    ("covenant_leverage", "Leverage screen", "vs. illustrative 4× / 6× bands"),
    ("covenant_coverage", "Coverage screen", "vs. illustrative 2× / 3× floor"),
]


def render_covenant_panel(brief: Brief) -> None:
    year = brief.fiscal_year
    st.subheader("Covenant screen")
    st.caption(
        "These are illustrative screening bands, not real covenants — real covenants "
        "live in the credit agreement, not the 10-K."
    )
    for concept, label, descriptor in _COVENANT_METRICS:
        fig = brief.fin.figures.get(make_figure_id(concept, year))
        render_metric_row(
            brief, concept, year, label, descriptor,
            value_md=_covenant_value_markdown(fig),
        )


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

# Reasoning-panel verdict -> (display text, semantic token). success/warning/danger
# read consistently with the credit-standing banner and the scorecard.
_SYNTHESIS_VERDICT = {
    "can_service": ("Can service", "success"),
    "conditional": ("Conditional", "warning"),
    "cannot_service": ("Cannot service", "danger"),
}
_ADDBACK_VERDICT = {
    "adjusted_fair": ("Adjusted EBITDA fair", "success"),
    "haircut_warranted": ("Haircut warranted", "warning"),
    "reject_adjustments": ("Reject adjustments", "danger"),
}

_CALLOUT = {"success": st.success, "neutral": st.info, "warning": st.warning, "danger": st.error}


def _figure_refs(citations: list | None) -> list[str]:
    """The figure_ids cited by a list of Citation objects (order-preserving, deduped)."""
    out: list[str] = []
    for c in citations or []:
        if getattr(c, "kind", None) == "figure" and c.ref and c.ref not in out:
            out.append(c.ref)
    return out


def _render_verdict(brief: Brief, mapping: dict, verdict: object, status: str | None) -> None:
    """Verdict as a clean semantic callout + the honest per-panel status badge."""
    display, token = mapping.get(
        verdict, (str(verdict or "—").replace("_", " ").capitalize(), "neutral")
    )
    _CALLOUT[token](f"**Verdict: {display}**")
    _render_panel_status(status)


def _render_panel_status(status: str | None) -> None:
    """Render the honest per-panel status badge (nothing for a clean 'ok')."""
    badge = _PANEL_STATUS_BADGE.get(status or "")
    if badge:
        st.markdown(badge)


def _render_source_chips(brief: Brief, figure_ids: list[str], clause_key: str) -> None:
    """A compact horizontal row of "🔍 <label>" chips — one per cited figure — each
    toggling the shared render_source drill-down inline below the row (no expander).

    Only figures that exist are chipped; wraps to a new row every few chips."""
    ids = [i for i in dict.fromkeys(figure_ids) if brief.fin.figures.get(i) is not None]
    if not ids:
        return

    per_row = 4
    opened: list[str] = []
    for start in range(0, len(ids), per_row):
        chunk = ids[start:start + per_row]
        cols = st.columns(_chip_weights(len(chunk)))
        for col, fid in zip(cols, chunk):
            concept = fid.split(":")[0]
            state_key = f"clausesrc::{clause_key}::{fid}"
            is_open = bool(st.session_state.get(state_key, False))
            with col:
                if st.button(f"🔍 {phrase_label(concept)}", key=_stable_key(f"btn::{state_key}")):
                    is_open = not is_open
                    st.session_state[state_key] = is_open
            if is_open:
                opened.append(fid)

    for fid in opened:
        render_source(brief, fid)


def _render_clause(
    brief: Brief, label: str, text: str, fallback_ids: list[str], clause_key: str
) -> None:
    """One reasoning clause: humanized prose + per-clause source chips.

    Chips come from the clause's own inline figure refs; a clause with none (e.g. the
    thesis) falls back to the panel-level citations so it is never left unanchored."""
    if label:
        st.markdown(f"**{label}**")
    clean, inline_ids = humanize_refs((text or "").strip())
    st.markdown(clean if clean else "_(no content surfaced this run)_")

    existing_inline = [i for i in inline_ids if brief.fin.figures.get(i) is not None]
    chip_ids = existing_inline or fallback_ids
    _render_source_chips(brief, chip_ids, clause_key)


def _render_section_citation(citation: object) -> None:
    """Keep the verbatim filing excerpt exactly as before (that part reads well)."""
    section = _SECTION_LABELS.get(citation.ref, citation.ref)
    excerpt = (citation.excerpt or "").strip()
    if excerpt:
        st.markdown(f"↳ verbatim from **{section}**:")
        st.markdown(f"> {excerpt}")
    else:
        st.caption(f"↳ cited to {section} (no excerpt supplied)")


def render_claim(brief: Brief, claim: object, year: int, clause_key: str = "") -> None:
    """A claim's humanized text, per-figure source chips, and verbatim section quotes.

    Figure citations (and any inline refs) become "🔍 <label>" chips opening the
    identical render_source drill-down; section citations quote the filing passage."""
    citations = getattr(claim, "citations", None) or []
    clean, inline_ids = humanize_refs((getattr(claim, "text", "") or "").strip())
    st.markdown(clean if clean else "_(no content surfaced this run)_")

    fig_ids = list(dict.fromkeys(inline_ids + _figure_refs(citations)))
    key = clause_key or f"claim::{fig_ids[0] if fig_ids else id(claim)}"
    _render_source_chips(brief, fig_ids, key)

    for citation in citations:
        if getattr(citation, "kind", None) == "section":
            _render_section_citation(citation)


def _render_caveats(brief: Brief, caveats: list, fallback_ids: list[str], clause_key: str) -> None:
    """Confidence caveats, each humanized with its own source chips."""
    caveats = [c for c in (caveats or []) if (c or "").strip()]
    if not caveats:
        return
    st.markdown("**Confidence caveats:**")
    for i, caveat in enumerate(caveats):
        _render_clause(brief, "", caveat, fallback_ids, f"{clause_key}.caveat{i}")


# --- Panel A: anchored synthesis (the money-shot, rendered first) -----------

def render_synthesis_panel(brief: Brief) -> None:
    env = brief.synthesis
    st.subheader("🧭 Panel A — Anchored credit synthesis")
    if env is None:
        st.info("Synthesis panel was not generated for this brief.")
        return
    panel = env.panel
    fallback = _figure_refs(getattr(panel, "citations", None))

    _render_verdict(brief, _SYNTHESIS_VERDICT, getattr(panel, "verdict", None), env.validation.status)

    _render_clause(brief, "Thesis", getattr(panel, "thesis", ""), fallback, "syn.thesis")
    _render_clause(brief, "Spine reading", getattr(panel, "spine_reading", ""), fallback, "syn.spine")
    _render_clause(brief, "Swing factor", getattr(panel, "swing_factor", ""), fallback, "syn.swing")
    _render_caveats(brief, getattr(panel, "confidence_caveats", []), fallback, "syn")


# --- Panel B: add-back adversary (bull vs skeptic) --------------------------

def render_addback_panel(brief: Brief) -> None:
    env = brief.addback_adversary
    st.subheader("⚖️ Panel B — Add-back adversary")
    if env is None:
        st.info("Add-back adversary panel was not generated for this brief.")
        return
    panel = env.panel
    fallback = _figure_refs(getattr(panel, "citations", None))

    _render_verdict(brief, _ADDBACK_VERDICT, getattr(panel, "verdict", None), env.validation.status)

    _render_clause(brief, "", getattr(panel, "headline", ""), fallback, "ab.headline")
    _render_clause(brief, "🟢 Accept case (bull)", getattr(panel, "accept_case", ""), fallback, "ab.accept")
    _render_clause(brief, "🔴 Challenge case (skeptic)", getattr(panel, "challenge_case", ""), fallback, "ab.challenge")
    _render_clause(brief, "Leverage read", getattr(panel, "leverage_read", ""), fallback, "ab.leverage")
    _render_clause(brief, "Excluded candidates", getattr(panel, "excluded_candidate_read", ""), fallback, "ab.excluded")
    _render_caveats(brief, getattr(panel, "confidence_caveats", []), fallback, "ab")


# --- Descriptive panels -----------------------------------------------------

def _render_claim_list(
    brief: Brief, claims: list, year: int, empty_msg: str, clause_key: str = "list"
) -> None:
    claims = list(claims or [])
    real = [c for c in claims if (getattr(c, "text", "") or "").strip()]
    if not real:
        st.markdown(f"_{empty_msg}_")
        return
    for i, claim in enumerate(real):
        render_claim(brief, claim, year, clause_key=f"{clause_key}.{i}")


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
        render_claim(brief, headline, year, clause_key="biz.headline")
    else:
        st.markdown("_(no headline surfaced this run)_")
    st.markdown("**Business lines**")
    _render_claim_list(brief, getattr(panel, "business_lines", []), year, "No segments surfaced this run.", "biz.lines")


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
        "No company-specific risks surfaced this run.", "risks",
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
    _render_claim_list(brief, getattr(panel, "drivers", []), year, "No drivers surfaced this run.", "rev.drivers")
    st.markdown("**Segment commentary**")
    _render_claim_list(brief, getattr(panel, "segment_commentary", []), year, "No segment commentary this run.", "rev.segments")


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
        "No candidates surfaced this run.", "qoe",
    )
