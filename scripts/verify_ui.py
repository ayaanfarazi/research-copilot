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
    for kind in ("title", "header", "subheader", "markdown", "caption", "text"):
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
    expected_band = (band.label or "").upper() if band is not None else ""
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
    subs = _subheaders(at)
    i_syn = _first_idx(subs, "Panel A")
    i_score = _first_idx(subs, "Credit scorecard")
    i_oper = _first_idx(subs, "Operating")
    i_bus = _first_idx(subs, "Business summary")

    exc_ok = len(at.exception) == 0
    print(f"\n[1] no uncaught exception (Credit) : {'OK' if exc_ok else 'FAIL'} "
          f"({len(at.exception)} exceptions)")
    if not exc_ok:
        for e in at.exception:
            print("      ", getattr(e, "value", e))

    band_ok = bool(expected_band) and expected_band in page_text
    print(f"[2] scorecard band renders         : {'OK' if band_ok else 'FAIL'} "
          f"(looking for {expected_band!r})")

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

    fig_src = [lbl for lbl in exp_labels if "figure source" in lbl]
    claim_src_ok = len(fig_src) >= 1
    print(f"[5] claim→figure source expander   : {'OK' if claim_src_ok else 'FAIL'} "
          f"({len(fig_src)} figure-source expanders)")

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
    cov_recipe_ok = (
        "EBITDA" in cov_src and "Interest expense" in cov_src
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

    print("\n  --- revenue source view (rendered text) ---")
    for line in rev_src.splitlines():
        print("    " + line)
    print("  --- interest_coverage source view (rendered text) ---")
    for line in cov_src.splitlines():
        print("    " + line)

    ok = (
        exc_ok and band_ok and exp_ok and panelA_ok and claim_src_ok and badge_ok
        and credit_order_ok and inv_exc_ok and investor_order_ok
        and rev_src_ok and cov_src_ok and clean_ok and fmt_ok
    )
    print("\n" + "=" * 70)
    print("GATE: ALL CHECKS PASSED" if ok else "GATE: FAILED")
    print("=" * 70)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
