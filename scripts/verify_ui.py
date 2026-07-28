#!/usr/bin/env python3
"""Phase 3 gate: the Streamlit renderer (deterministic figures + LLM panels + view toggle).

Runs app.py headless via streamlit.testing.v1.AppTest against the cached MSFT
brief (zero API calls — use_cache=True in the app). Asserts:

  Content (Credit view, default):
    1. no uncaught exception,
    2. the scorecard band text renders,
    3. at least one expand-to-source element is present,
    4. Panel A (synthesis) content renders (verdict + thesis),
    5. at least one claim→figure source expander is present,
    6. a validation_failed / empty panel renders its honest badge (no crash).

  View toggle (Step 4 — reorder only, zero recompute):
    7. Credit view: the synthesis/scorecard block appears before the operating block,
    8. Investor view: no uncaught exception,
    9. Investor view: the operating/business block appears before the synthesis block.

Prints a compact inventory and the full result.
"""

from __future__ import annotations

import sys

sys.path.insert(0, ".")

import re

from streamlit.testing.v1 import AppTest

from src.brief import assemble_brief
from src.data.models import make_figure_id
from src.ui.format import fmt_money, fmt_multiple, sec_filing_url
from src.ui.render import _SYNTHESIS_VERDICT

_HONEST_BADGE_SUBSTR = "did not fully validate this run"

# A raw un-rounded float (>= 6 fractional digits) and a bare figure_id must never
# reach the default source view — both live only behind the technical-details toggle.
_RAW_FLOAT_RE = re.compile(r"\d+\.\d{6,}")
_FIGURE_ID_RE = re.compile(r"[a-z_]+:FY\d{4}")

# The raw figure_id token that must never leak into humanized AI-panel prose.
_AI_REF_RE = re.compile(r"[A-Za-z_]+:FY20\d{2}")

_REVENUE_TAG = "RevenueFromContractWithCustomerExcludingAssessedTax"


# --- Isolated source-view harnesses -----------------------------------------
# render_source is exercised on its OWN so the assertions read only the provenance
# body (not the surrounding page or LLM-panel prose, which is a later packet).

def _revenue_source_app() -> None:
    from src.brief import assemble_brief as _assemble
    from src.data.models import make_figure_id as _mid
    from src.ui.render import begin_render_run, render_source

    begin_render_run()
    b = _assemble("MSFT", use_cache=True)
    render_source(b, _mid("revenue", b.fiscal_year))


def _interest_coverage_source_app() -> None:
    from src.brief import assemble_brief as _assemble
    from src.data.models import make_figure_id as _mid
    from src.ui.render import begin_render_run, render_source

    begin_render_run()
    b = _assemble("MSFT", use_cache=True)
    render_source(b, _mid("interest_coverage", b.fiscal_year))


def _scorecard_app() -> None:
    from src.brief import assemble_brief as _assemble
    from src.ui.render import begin_render_run, render_scorecard_band

    begin_render_run()
    render_scorecard_band(_assemble("MSFT", use_cache=True))


def _scorecard_withheld_app() -> None:
    # Synthetic degraded case: mutate the cached band to a withheld state (as the
    # pipeline does for financial issuers) — pure rendering, still zero API.
    from src.brief import assemble_brief as _assemble
    from src.data.models import make_figure_id as _mid
    from src.ui.render import begin_render_run, render_scorecard_band

    begin_render_run()
    b = _assemble("MSFT", use_cache=True)
    band = b.fin.figures.get(_mid("credit_band", b.fiscal_year))
    band.value = None
    band.status = "not_found"
    band.label = "not_applicable_financial"
    band.notes = [
        "credit scorecard not applicable to financial issuers "
        "(SIC 6020: State commercial banks); industrial leverage/coverage framing does not fit"
    ]
    render_scorecard_band(b)


def _key_metrics_app() -> None:
    from src.brief import assemble_brief as _assemble
    from src.ui.render import begin_render_run, render_credit_panel

    begin_render_run()
    render_credit_panel(_assemble("MSFT", use_cache=True))


def _metric_row_app() -> None:
    from src.brief import assemble_brief as _assemble
    from src.ui.render import begin_render_run, render_metric_row

    begin_render_run()
    b = _assemble("MSFT", use_cache=True)
    render_metric_row(
        b, "interest_coverage", b.fiscal_year, "Interest coverage",
        "how many times earnings cover the interest bill",
    )


def _survival_app() -> None:
    from src.brief import assemble_brief as _assemble
    from src.ui.render import begin_render_run, render_survival_panel

    begin_render_run()
    render_survival_panel(_assemble("MSFT", use_cache=True))


def _covenant_app() -> None:
    from src.brief import assemble_brief as _assemble
    from src.ui.render import begin_render_run, render_covenant_panel

    begin_render_run()
    render_covenant_panel(_assemble("MSFT", use_cache=True))


def _bridge_app() -> None:
    from src.brief import assemble_brief as _assemble
    from src.ui.render import begin_render_run, render_ebitda_bridge

    begin_render_run()
    render_ebitda_bridge(_assemble("MSFT", use_cache=True))


def _synthesis_app() -> None:
    from src.brief import assemble_brief as _assemble
    from src.ui.render import begin_render_run, render_synthesis_panel

    begin_render_run()
    render_synthesis_panel(_assemble("MSFT", use_cache=True))


def _addback_app() -> None:
    from src.brief import assemble_brief as _assemble
    from src.ui.render import begin_render_run, render_addback_panel

    begin_render_run()
    render_addback_panel(_assemble("MSFT", use_cache=True))


def _run_formatter_unit_checks() -> bool:
    """Pure-Python checks on the formatters (no Streamlit, no API)."""
    cases = [
        ("fmt_multiple(44.0998)", fmt_multiple(44.0998), "44.1×"),
        ("fmt_money(2_935_000_000)", fmt_money(2_935_000_000), "$2,935M"),
        ("fmt_money(129_433_000_000)", fmt_money(129_433_000_000), "$129.4B"),
        ("fmt_money(-2_935_000_000)", fmt_money(-2_935_000_000), "-$2,935M"),
        ("fmt_money(None)", fmt_money(None), "not found"),
        (
            'sec_filing_url("0000789019","0000950170-24-087843")',
            sec_filing_url("0000789019", "0000950170-24-087843"),
            "https://www.sec.gov/Archives/edgar/data/789019/"
            "000095017024087843/0000950170-24-087843-index.htm",
        ),
        ("sec_filing_url(None, acc)", sec_filing_url(None, "0000950170-24-087843"), None),
        ("sec_filing_url(cik, None)", sec_filing_url("0000789019", None), None),
    ]
    all_ok = True
    for name, got, want in cases:
        ok = got == want
        all_ok = all_ok and ok
        line = f"    {'OK  ' if ok else 'FAIL'} {name} -> {got!r}"
        if not ok:
            line += f"   (want {want!r})"
        print(line)
    return all_ok


def _all_text(at: AppTest) -> str:
    chunks: list[str] = []
    # Includes the semantic callout banners (success/info/warning/error) so the
    # credit-standing banner text is visible to page-level substring checks.
    for kind in ("title", "header", "subheader", "markdown", "caption", "text",
                 "success", "info", "warning", "error"):
        try:
            for el in getattr(at, kind):
                v = getattr(el, "value", None)
                if isinstance(v, str):
                    chunks.append(v)
        except Exception:
            pass
    return "\n".join(chunks)


def _subheaders(at: AppTest) -> list[str]:
    """Subheader values in document (top-to-bottom) order — the section sequence."""
    out: list[str] = []
    try:
        for el in at.subheader:
            v = getattr(el, "value", None)
            if isinstance(v, str):
                out.append(v)
    except Exception:
        pass
    return out


def _first_idx(subs: list[str], needle: str) -> int:
    for i, s in enumerate(subs):
        if needle in s:
            return i
    return -1


def _expander_labels(at: AppTest) -> list[str]:
    out: list[str] = []
    try:
        for el in at.expander:
            lbl = getattr(el, "label", None)
            if isinstance(lbl, str):
                out.append(lbl)
    except Exception:
        pass
    return out


def main() -> int:
    print("=" * 70)
    print("PHASE 3 GATE — Streamlit renderer + Investor/Credit view toggle (MSFT)")
    print("=" * 70)

    # Expected values read straight from the cached brief (zero API calls).
    brief = assemble_brief("MSFT", use_cache=True)
    band = brief.fin.figures.get(make_figure_id("credit_band", brief.fiscal_year))
    expected_band = (band.label or "") if band is not None else ""
    syn = brief.synthesis.panel
    expected_verdict = _SYNTHESIS_VERDICT.get(syn.verdict, (syn.verdict.upper(), ""))[0]
    thesis_fragment = (syn.thesis or "")[:40].strip()
    nonok_panels = [
        name for name in
        ("synthesis", "addback_adversary", "business_summary", "risks",
         "revenue_drivers", "qoe_candidates")
        if getattr(brief, name).validation.status != "ok"
    ]
    print(f"\ncached MSFT anchor FY{brief.fiscal_year}")
    print(f"  band label       : {expected_band!r}")
    print(f"  synthesis verdict: {syn.verdict!r} -> {expected_verdict!r}")
    print(f"  non-ok panels    : {nonok_panels}")

    # ------------------------------------------------------------------ Credit
    at = AppTest.from_file("app.py", default_timeout=60)
    at.run()  # default view = Credit

    page_text = _all_text(at)
    exp_labels = _expander_labels(at)
    credit_warnings = [w.value for w in at.warning]  # Credit-view amber callouts
    credit_pills = sum(1 for b in at.button if b.label == "🔍 source")
    subs = _subheaders(at)
    i_syn = _first_idx(subs, "Panel A")
    i_score = _first_idx(subs, "the scorecard")  # "Why <band> — the scorecard"
    i_oper = _first_idx(subs, "Operating")
    i_bus = _first_idx(subs, "Business summary")

    exc_ok = len(at.exception) == 0
    print(f"\n[1] no uncaught exception (Credit) : {'OK' if exc_ok else 'FAIL'} "
          f"({len(at.exception)} exceptions)")
    if not exc_ok:
        for e in at.exception:
            print("      ", getattr(e, "value", e))

    band_banner = f"Credit standing: {expected_band}"
    band_ok = bool(expected_band) and band_banner in page_text
    print(f"[2] credit-standing banner renders : {'OK' if band_ok else 'FAIL'} "
          f"(looking for {band_banner!r})")

    exp_ok = len(exp_labels) >= 1
    print(f"[3] expand-to-source present       : {'OK' if exp_ok else 'FAIL'} "
          f"({len(exp_labels)} expanders)")

    panelA_ok = (
        i_syn >= 0
        and bool(expected_verdict) and expected_verdict in page_text
        and bool(thesis_fragment) and thesis_fragment in page_text
    )
    print(f"[4] Panel A synthesis content      : {'OK' if panelA_ok else 'FAIL'} "
          f"(verdict {expected_verdict!r} + thesis fragment)")

    # Titles are now plain ("🔍 source" / "🔍 source — <label>"), never a raw
    # figure_id — so count the shared source drill-down affordance by prefix.
    fig_src = [lbl for lbl in exp_labels if lbl.startswith("🔍 source")]
    claim_src_ok = len(fig_src) >= 1
    print(f"[5] source drill-down affordance    : {'OK' if claim_src_ok else 'FAIL'} "
          f"({len(fig_src)} source expanders)")

    badge_ok = bool(nonok_panels) and _HONEST_BADGE_SUBSTR in page_text
    print(f"[6] honest badge on non-ok panel   : {'OK' if badge_ok else 'FAIL'} "
          f"(present={_HONEST_BADGE_SUBSTR in page_text})")

    # Ordering: synthesis + scorecard before operating.
    credit_order_ok = (
        i_syn >= 0 and i_score >= 0 and i_oper >= 0
        and i_syn < i_oper and i_score < i_oper
    )
    print(f"[7] Credit order: A/scorecard<oper : {'OK' if credit_order_ok else 'FAIL'} "
          f"(scorecard@{i_score}, synthesis@{i_syn}, operating@{i_oper})")

    # ---------------------------------------------------------------- Investor
    at.radio[0].set_value("Investor").run()
    inv_page_text = _all_text(at)  # for the AI-ref token scan in the other view

    inv_exc_ok = len(at.exception) == 0
    print(f"\n[8] no uncaught exception (Investor): {'OK' if inv_exc_ok else 'FAIL'} "
          f"({len(at.exception)} exceptions)")
    if not inv_exc_ok:
        for e in at.exception:
            print("      ", getattr(e, "value", e))

    isubs = _subheaders(at)
    j_syn = _first_idx(isubs, "Panel A")
    j_oper = _first_idx(isubs, "Operating")
    j_bus = _first_idx(isubs, "Business summary")
    investor_order_ok = (
        j_syn >= 0 and j_oper >= 0 and j_bus >= 0
        and j_oper < j_syn and j_bus < j_syn
    )
    print(f"[9] Investor order: oper/bus<A     : {'OK' if investor_order_ok else 'FAIL'} "
          f"(business@{j_bus}, operating@{j_oper}, synthesis@{j_syn})")

    print("\n  --- section order (Credit) ---")
    print("    " + " → ".join(subs))
    print("  --- section order (Investor) ---")
    print("    " + " → ".join(isubs))

    # ---------------------------------------------- Packet A: source drill-down
    # Render the two source views in isolation and assert on the provenance body.
    print("\n" + "-" * 70)
    print("PACKET A — provenance-first source drill-down + formatting")
    print("-" * 70)

    at_rev = AppTest.from_function(_revenue_source_app, default_timeout=60)
    at_rev.run()
    rev_src = _all_text(at_rev)
    at_cov = AppTest.from_function(_interest_coverage_source_app, default_timeout=60)
    at_cov.run()
    cov_src = _all_text(at_cov)

    rev_exc_ok = len(at_rev.exception) == 0
    cov_exc_ok = len(at_cov.exception) == 0
    for label, atx in (("revenue", at_rev), ("interest_coverage", at_cov)):
        for e in atx.exception:
            print(f"      [{label}] EXC", getattr(e, "value", e))

    # [10] revenue fact source: XBRL tag + a real SEC EDGAR filing URL.
    rev_url_ok = "sec.gov/Archives/edgar" in rev_src
    rev_tag_ok = _REVENUE_TAG in rev_src
    rev_src_ok = rev_exc_ok and rev_tag_ok and rev_url_ok
    print(f"\n[10] revenue source: XBRL tag + SEC URL : {'OK' if rev_src_ok else 'FAIL'} "
          f"(tag={rev_tag_ok}, sec_url={rev_url_ok})")

    # [11] interest_coverage metric source: recipe with component labels + result.
    #      Labels read in-sentence casing ("interest expense", acronym "EBITDA").
    cov_recipe_ok = (
        "EBITDA" in cov_src and "interest expense" in cov_src
        and "÷" in cov_src and "= 44.1×" in cov_src
    )
    cov_src_ok = cov_exc_ok and cov_recipe_ok
    print(f"[11] interest_coverage source: recipe   : {'OK' if cov_src_ok else 'FAIL'} "
          f"(labels+÷+result={cov_recipe_ok})")

    # [12] no raw 6-decimal float and no literal figure_id in the source text.
    combined_src = rev_src + "\n" + cov_src
    no_raw_float = _RAW_FLOAT_RE.search(combined_src) is None
    no_fig_id = _FIGURE_ID_RE.search(combined_src) is None
    clean_ok = no_raw_float and no_fig_id
    print(f"[12] no raw float / no figure_id shown  : {'OK' if clean_ok else 'FAIL'} "
          f"(no_raw_float={no_raw_float}, no_figure_id={no_fig_id})")
    if not no_raw_float:
        print("      raw float:", _RAW_FLOAT_RE.search(combined_src).group(0))
    if not no_fig_id:
        print("      figure_id:", _FIGURE_ID_RE.search(combined_src).group(0))

    # [13] pure-Python formatter/link unit checks.
    print("[13] formatter + SEC-link unit checks   :")
    fmt_ok = _run_formatter_unit_checks()
    print(f"      -> {'OK' if fmt_ok else 'FAIL'}")

    # -------------------------------- Packet A.1: match the approved mockup look
    print("\n" + "-" * 70)
    print("PACKET A.1 — clean recipe sentence, compact chips, plain titles")
    print("-" * 70)

    # [14] interest_coverage recipe in the parallel "Computed as … = … = 44.1×" form
    #      (label expression, then value expression, then result — never interleaved).
    interest_val_ok = "$2,935M" in cov_src or "$2.9B" in cov_src
    recipe_form_ok = (
        "Computed as EBITDA ÷ interest expense" in cov_src
        and "$129.4B ÷ " in cov_src
        and "= 44.1×" in cov_src
        and interest_val_ok
    )
    print(f"[14] recipe 'Computed as … = … = 44.1×' : {'OK' if recipe_form_ok else 'FAIL'} "
          f"(129.4B={'$129.4B ÷ ' in cov_src}, interest_val={interest_val_ok})")

    # [15] the old "Trace each input:" heading is gone (isolated view + full page).
    trace_heading_gone = (
        "Trace each input:" not in cov_src and "Trace each input:" not in page_text
    )
    print(f"[15] 'Trace each input:' heading removed: {'OK' if trace_heading_gone else 'FAIL'}")

    # [16] no rendered expander title exposes a concept:FY20xx figure_id (both views).
    inv_labels = _expander_labels(at)
    all_titles = exp_labels + inv_labels
    title_id_hits = [t for t in all_titles if re.search(r"[a-z_]+:FY20\d\d", t)]
    titles_clean_ok = not title_id_hits
    print(f"[16] no figure_id in any source title   : {'OK' if titles_clean_ok else 'FAIL'} "
          f"({len(all_titles)} titles scanned)")
    if title_id_hits:
        print("      offending:", title_id_hits[:3])

    # [17] the ratio recipe is NOT interleaved: values are grouped after the "=",
    #      so a "label value" adjacency like "EBITDA $129.4B" must not appear in text.
    not_interleaved = "EBITDA $129.4B" not in cov_src and "interest expense $2" not in cov_src
    print(f"[17] ratio recipe not interleaved       : {'OK' if not_interleaved else 'FAIL'}")

    # [18] a metric's fact input still traces to the leaf card with a SEC.gov link.
    at_leaf = AppTest.from_function(_interest_coverage_source_app, default_timeout=60)
    at_leaf.run()
    for btn in at_leaf.button:
        if btn.label.startswith("Interest expense"):
            btn.click().run()
            break
    leaf_src = _all_text(at_leaf)
    leaf_ok = (
        len(at_leaf.exception) == 0
        and "InterestExpense" in leaf_src
        and "sec.gov/Archives/edgar" in leaf_src
    )
    print(f"[18] trace → fact leaf card + SEC link   : {'OK' if leaf_ok else 'FAIL'} "
          f"({len(at_leaf.exception)} exceptions on drill)")
    for e in at_leaf.exception:
        print("      EXC", getattr(e, "value", e))

    # ------------------------- Packet B: credit-standing banner + scorecard tiles
    print("\n" + "-" * 70)
    print("PACKET B — credit-standing banner + scorecard-as-tiles")
    print("-" * 70)

    at_sc = AppTest.from_function(_scorecard_app, default_timeout=60)
    at_sc.run()
    sc_md = [m.value for m in at_sc.markdown if isinstance(m.value, str)]
    sc_cap = [c.value for c in at_sc.caption if isinstance(c.value, str)]
    sc_success = [s.value for s in at_sc.success]
    sc_text = _all_text(at_sc)
    sc_exp = _expander_labels(at_sc)
    sc_exc_ok = len(at_sc.exception) == 0
    for e in at_sc.exception:
        print("      [scorecard] EXC", getattr(e, "value", e))

    # [19] banner renders "Credit standing: strong" with a success (green) treatment.
    banner_ok = (
        sc_exc_ok
        and any("Credit standing: strong" in v for v in sc_success)
        and len(at_sc.success) == 1
        and len(at_sc.info) == 0 and len(at_sc.error) == 0 and len(at_sc.warning) == 0
    )
    print(f"[19] banner 'Credit standing: strong' (success): {'OK' if banner_ok else 'FAIL'} "
          f"(success={len(at_sc.success)}, info={len(at_sc.info)})")

    # [20] four dimension tiles render, each with a colored word-tier.
    joined_md = "\n".join(sc_md)
    names_ok = all(f":gray[{n}]" in joined_md for n in ("Leverage", "Coverage", "Trajectory", "Liquidity"))
    tier_word_lines = [v for v in sc_md if v.startswith("### :")]
    tiles_ok = names_ok and len(tier_word_lines) == 4
    print(f"[20] four word-tier dimension tiles     : {'OK' if tiles_ok else 'FAIL'} "
          f"(names={names_ok}, tiers={len(tier_word_lines)})")

    # [21] scorecard text is clean: no severity(0-3), no raw float, no figure_id.
    sc_no_sev = "severity" not in sc_text
    sc_no_float = _RAW_FLOAT_RE.search(sc_text) is None
    sc_no_id = _FIGURE_ID_RE.search(sc_text) is None
    sc_clean_ok = sc_no_sev and sc_no_float and sc_no_id
    print(f"[21] tiles clean (no sev/float/fig_id)  : {'OK' if sc_clean_ok else 'FAIL'} "
          f"(no_severity={sc_no_sev}, no_float={sc_no_float}, no_figure_id={sc_no_id})")

    # [22] the plain-English rule line renders below the tiles.
    rule_ok = any(v.startswith("Rule:") for v in sc_cap)
    print(f"[22] rule line renders                  : {'OK' if rule_ok else 'FAIL'}")

    # [23] the band still exposes a working render_source drill-down.
    source_ok = (
        any(l.startswith("🔍 source") for l in sc_exp)
        and any("Computed" in v for v in sc_md)
    )
    print(f"[23] band render_source still works     : {'OK' if source_ok else 'FAIL'} "
          f"(source expander + recipe present)")

    # [24] degraded: a withheld band renders a neutral informational banner
    #      (st.info), never a colored verdict (no success/warning/error).
    at_wh = AppTest.from_function(_scorecard_withheld_app, default_timeout=60)
    at_wh.run()
    wh_info = [i.value for i in at_wh.info]
    withheld_ok = (
        len(at_wh.exception) == 0
        and len(at_wh.info) >= 1
        and len(at_wh.success) == 0 and len(at_wh.error) == 0 and len(at_wh.warning) == 0
        and any("not assessed" in v for v in wh_info)
    )
    print(f"[24] withheld band → info, not verdict  : {'OK' if withheld_ok else 'FAIL'} "
          f"(info={len(at_wh.info)}, success={len(at_wh.success)}, error={len(at_wh.error)})")

    # ---------------- Packet C: page header + metric-row component + key metrics
    print("\n" + "-" * 70)
    print("PACKET C — page header + metric-row component + Key credit metrics")
    print("-" * 70)

    # [25] header: title-cased entity name + the muted provenance subline.
    header_ok = (
        "Microsoft Corporation" in page_text
        and "MSFT · fiscal year 2024 · figures from SEC filings" in page_text
    )
    print(f"[25] header name + subline render       : {'OK' if header_ok else 'FAIL'}")

    # [26] key-metric rows: header + one "🔍 source" pill per row + formatted values.
    rows_ok = (
        "Key credit metrics" in page_text
        and credit_pills >= 9
        and "44.1×" in page_text and "Net cash" in page_text and "$75.5B" in page_text
    )
    print(f"[26] key-metric rows + source pills     : {'OK' if rows_ok else 'FAIL'} "
          f"({credit_pills} pills)")

    # [27] toggling a row's source pill reveals a drill-down reaching a SEC link.
    at_row = AppTest.from_function(_metric_row_app, default_timeout=60)
    at_row.run()
    for b in at_row.button:
        if b.label == "🔍 source":
            b.click().run()
            break
    for b in at_row.button:  # after the pill opens, the recipe chips appear
        if b.label.startswith("Interest expense"):
            b.click().run()
            break
    row_src = _all_text(at_row)
    row_drill_ok = (
        len(at_row.exception) == 0
        and "InterestExpense" in row_src
        and "sec.gov/Archives/edgar" in row_src
    )
    print(f"[27] pill → inline drill-down → SEC link : {'OK' if row_drill_ok else 'FAIL'} "
          f"({len(at_row.exception)} exceptions)")
    for e in at_row.exception:
        print("      EXC", getattr(e, "value", e))

    # [28] the maturity-wall degraded state renders as the amber honest box.
    mw_ok = any("Maturity wall — limited detail" in v for v in credit_warnings)
    print(f"[28] maturity-wall honest box (amber)   : {'OK' if mw_ok else 'FAIL'} "
          f"({len(credit_warnings)} warnings)")

    # [29] the key-metrics block default view is clean (no fig_id/severity/float).
    at_km = AppTest.from_function(_key_metrics_app, default_timeout=60)
    at_km.run()
    km_text = _all_text(at_km)
    km_clean_ok = (
        len(at_km.exception) == 0
        and "severity" not in km_text
        and _RAW_FLOAT_RE.search(km_text) is None
        and _FIGURE_ID_RE.search(km_text) is None
    )
    print(f"[29] key-metrics default view clean     : {'OK' if km_clean_ok else 'FAIL'} "
          f"(no severity/float/figure_id)")

    # -------- Packet C2: survival + covenant + EBITDA bridge in the row pattern
    print("\n" + "-" * 70)
    print("PACKET C2 — survival + covenant + EBITDA bridge as metric rows")
    print("-" * 70)

    def _section(fn):
        a = AppTest.from_function(fn, default_timeout=60)
        a.run()
        md = [m.value for m in a.markdown if isinstance(m.value, str)]
        cap = [c.value for c in a.caption if isinstance(c.value, str)]
        pills = sum(1 for b in a.button if b.label == "🔍 source")
        return a, md, cap, pills

    at_sv, sv_md, sv_cap, sv_pills = _section(_survival_app)
    at_cv, cv_md, cv_cap, cv_pills = _section(_covenant_app)
    at_br, br_md, br_cap, br_pills = _section(_bridge_app)

    # [30] survival rows: word-tiers + a source pill each, no raw float / figure_id.
    sv_text = _all_text(at_sv)
    sv_tiers = all(w in "\n".join(sv_md) for w in ("Worsening", "Strengthening", "Comfortable"))
    sv_ok = (
        len(at_sv.exception) == 0 and sv_tiers and sv_pills >= 3
        and _RAW_FLOAT_RE.search(sv_text) is None and _FIGURE_ID_RE.search(sv_text) is None
    )
    print(f"[30] survival rows: word-tiers + pills  : {'OK' if sv_ok else 'FAIL'} "
          f"(tiers={sv_tiers}, pills={sv_pills})")

    # [31] covenant rows: the illustrative / not-real-covenants label + rows + pills.
    cov_label = any("illustrative" in c and "not real covenants" in c for c in cv_cap)
    cv_ok = len(at_cv.exception) == 0 and cov_label and cv_pills >= 2 and "within band" in "\n".join(cv_md)
    print(f"[31] covenant rows + illustrative label : {'OK' if cv_ok else 'FAIL'} "
          f"(label={cov_label}, pills={cv_pills})")

    # [32] EBITDA-bridge titles/labels clean: no snake_case token, no leading "+ ".
    br_all = "\n".join(br_md + [b.label for b in at_br.button] + [e.label or "" for e in at_br.expander])
    bridge_labels_clean = (
        "amortization_intangibles" not in br_all
        and "+ " not in br_all
        and "Amortization of intangibles" in "\n".join(br_md)
        and all(b.label == "🔍 source" for b in at_br.button)
    )
    print(f"[32] EBITDA-bridge titles are plain     : {'OK' if bridge_labels_clean else 'FAIL'}")

    # [33] each restyled section's source pill reaches provenance; the bridge's fact
    #      row reaches a real SEC.gov link directly.
    for b in at_br.button:
        if b.label == "🔍 source":
            b.click().run()
            break
    br_drill = _all_text(at_br)
    bridge_sec_ok = len(at_br.exception) == 0 and "sec.gov/Archives/edgar" in br_drill
    # survival + covenant pills open the shared source body (recipe/fact content).
    for b in at_sv.button:
        if b.label == "🔍 source":
            b.click().run()
            break
    sv_drill = _all_text(at_sv)
    for b in at_cv.button:
        if b.label == "🔍 source":
            b.click().run()
            break
    cv_drill = _all_text(at_cv)
    drill_open = (
        len(at_sv.exception) == 0 and len(at_cv.exception) == 0
        and any(k in sv_drill for k in ("Computed", "Reported value", "XBRL tag"))
        and any(k in cv_drill for k in ("Computed", "Reported value", "XBRL tag"))
    )
    pill_ok = bridge_sec_ok and drill_open
    print(f"[33] source pills reach provenance/SEC  : {'OK' if pill_ok else 'FAIL'} "
          f"(bridge_sec={bridge_sec_ok}, surv/cov drill={drill_open})")

    print("\n  --- survival rows ---")
    for lbl, val in zip(sv_md[0::2], sv_md[1::2]):
        print(f"    {lbl.strip('*'):26} {val}")
    print("  --- covenant rows ---")
    for c in cv_cap:
        print("    [caption] " + c)
    for lbl, val in zip(cv_md[0::2], cv_md[1::2]):
        print(f"    {lbl.strip('*'):26} {val}")
    print("  --- EBITDA bridge rows ---")
    for lbl, val in zip(br_md[0::2], br_md[1::2]):
        print(f"    {lbl.strip('*'):30} {val}")

    # ------------------------------- Packet D: humanize the AI-panel claims
    print("\n" + "-" * 70)
    print("PACKET D — humanize AI-panel claims (inline refs → labels + source chips)")
    print("-" * 70)

    # [34] no raw figure_id token leaks into the AI-panel prose, in EITHER view.
    cred_tokens = sorted(set(_AI_REF_RE.findall(page_text)))
    inv_tokens = sorted(set(_AI_REF_RE.findall(inv_page_text)))
    no_tokens_ok = not cred_tokens and not inv_tokens
    print(f"[34] no figure_id token in AI prose     : {'OK' if no_tokens_ok else 'FAIL'} "
          f"(credit={len(cred_tokens)}, investor={len(inv_tokens)})")
    if cred_tokens or inv_tokens:
        print("      leftover:", (cred_tokens or inv_tokens)[:5])

    # [35] a reasoning clause with inline refs renders source chips that open
    #      render_source; a fact-backed chip reaches a real SEC.gov link.
    at_syn = AppTest.from_function(_synthesis_app, default_timeout=90)
    at_syn.run()
    syn_chips = [b.label for b in at_syn.button if b.label.startswith("🔍 ")]
    for b in at_syn.button:
        if b.label == "🔍 capital expenditure":  # capex is a raw fact -> direct SEC link
            b.click().run()
            break
    syn_drill = _all_text(at_syn)
    chips_ok = (
        len(at_syn.exception) == 0 and len(syn_chips) >= 3
        and "sec.gov/Archives/edgar" in syn_drill and "XBRL tag" in syn_drill
    )
    print(f"[35] clause source chips → render_source: {'OK' if chips_ok else 'FAIL'} "
          f"({len(syn_chips)} chips, capex chip reaches SEC)")

    # [36] each verdict renders as its semantic callout (synthesis→success,
    #      add-back→warning for MSFT's can_service / haircut_warranted).
    syn_verdict_ok = any("Verdict: Can service" in s.value for s in at_syn.success)
    at_ab = AppTest.from_function(_addback_app, default_timeout=90)
    at_ab.run()
    ab_verdict_ok = any("Verdict: Haircut warranted" in w.value for w in at_ab.warning)
    ab_no_tokens = not _AI_REF_RE.search(_all_text(at_ab))
    verdict_ok = (
        len(at_syn.exception) == 0 and len(at_ab.exception) == 0
        and syn_verdict_ok and ab_verdict_ok and ab_no_tokens
    )
    print(f"[36] verdicts render semantic colours   : {'OK' if verdict_ok else 'FAIL'} "
          f"(syn=success:{syn_verdict_ok}, addback=warning:{ab_verdict_ok})")

    # [37] the honest per-panel status badge still shows for a non-ok panel.
    badge_still_ok = _HONEST_BADGE_SUBSTR in page_text
    print(f"[37] panel status badge still shows     : {'OK' if badge_still_ok else 'FAIL'}")

    print("\n  --- Panel A (synthesis) humanized ---")
    for m in at_syn.markdown[:8]:
        if isinstance(m.value, str):
            print("    " + m.value[:150])
    print("    verdict callout:", [s.value for s in at_syn.success])
    print("    chips:", syn_chips[:14])
    print("  --- Panel B (add-back) verdict ---")
    print("    verdict callout:", [w.value for w in at_ab.warning if "Verdict" in w.value])

    print("\n  --- revenue source view (rendered text) ---")
    for line in rev_src.splitlines():
        print("    " + line)
    print("  --- interest_coverage source view (rendered text) ---")
    for line in cov_src.splitlines():
        print("    " + line)
    print("  --- interest_coverage after tracing 'Interest expense' ---")
    for line in leaf_src.splitlines():
        print("    " + line)
    print("  --- credit-standing banner + scorecard tiles ---")
    for v in sc_success:
        print("    [banner] " + v.replace("\n", " ⏎ "))
    for name, tier in zip([v for v in sc_md if v.startswith(":gray[")], tier_word_lines):
        print(f"    [tile] {name}  →  {tier}")
    for v in sc_cap:
        print("    [caption] " + v)
    print("  --- withheld band banner ---")
    for v in wh_info:
        print("    [info] " + v.replace("\n", " ⏎ "))
    print("  --- key-metrics rows (label · descriptor · value) ---")
    km_md = [m.value for m in at_km.markdown if isinstance(m.value, str)]
    km_cap = [c.value for c in at_km.caption if isinstance(c.value, str)]
    # Each row emits markdown [label, value] in order; captions are the descriptors.
    for lbl, val, desc in zip(km_md[0::2], km_md[1::2], km_cap):
        print(f"    {lbl.strip('*'):24} {desc:52} {val}")
    print("  --- maturity-wall honest box ---")
    for v in credit_warnings:
        print("    [warning] " + v.replace("\n", " "))

    ok = (
        exc_ok and band_ok and exp_ok and panelA_ok and claim_src_ok and badge_ok
        and credit_order_ok and inv_exc_ok and investor_order_ok
        and rev_src_ok and cov_src_ok and clean_ok and fmt_ok
        and recipe_form_ok and trace_heading_gone and titles_clean_ok
        and not_interleaved and leaf_ok
        and banner_ok and tiles_ok and sc_clean_ok and rule_ok and source_ok and withheld_ok
        and header_ok and rows_ok and row_drill_ok and mw_ok and km_clean_ok
        and sv_ok and cv_ok and bridge_labels_clean and pill_ok
        and no_tokens_ok and chips_ok and verdict_ok and badge_still_ok
    )
    print("\n" + "=" * 70)
    print("GATE: ALL CHECKS PASSED" if ok else "GATE: FAILED")
    print("=" * 70)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
