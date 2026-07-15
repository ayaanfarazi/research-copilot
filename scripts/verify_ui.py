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

from streamlit.testing.v1 import AppTest

from src.brief import assemble_brief
from src.data.models import make_figure_id
from src.ui.render import _SYNTHESIS_VERDICT

_HONEST_BADGE_SUBSTR = "did not fully validate this run"


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

    ok = (
        exc_ok and band_ok and exp_ok and panelA_ok and claim_src_ok and badge_ok
        and credit_order_ok and inv_exc_ok and investor_order_ok
    )
    print("\n" + "=" * 70)
    print("GATE: ALL CHECKS PASSED" if ok else "GATE: FAILED")
    print("=" * 70)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
