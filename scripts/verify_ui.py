#!/usr/bin/env python3
"""Phase 3 gate: the Streamlit renderer (deterministic figures + six LLM panels).

Runs app.py headless via streamlit.testing.v1.AppTest against the cached MSFT
brief (zero API calls — use_cache=True in the app). Asserts:

  Step 2 (deterministic):
    1. no uncaught exception (at.exception is empty),
    2. the scorecard band text renders,
    3. at least one expand-to-source element (st.expander) is present.

  Step 3 (LLM panels + claim→source):
    4. Panel A (synthesis) content renders (verdict + thesis),
    5. at least one claim→figure source expander is present,
    6. a validation_failed / empty panel renders its honest badge (no crash).

Prints a compact inventory and the full result.
"""

from __future__ import annotations

import sys

sys.path.insert(0, ".")

from streamlit.testing.v1 import AppTest

from src.brief import assemble_brief
from src.data.models import make_figure_id
from src.ui.render import _SYNTHESIS_VERDICT

# The honest per-panel badge text (substring) rendered for a non-ok panel.
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
    print("PHASE 3 GATE — Streamlit renderer: deterministic + LLM panels (MSFT)")
    print("=" * 70)

    # Expected values read straight from the cached brief (zero API calls) so the
    # assertions are not hard-coded to stale content.
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
    print(f"  synthesis verdict: {syn.verdict!r} -> {expected_verdict!r}, {len(syn.citations)} figure citations")
    print(f"  non-ok panels    : {nonok_panels}")

    # Run the app headless (default selectbox = MSFT).
    at = AppTest.from_file("app.py", default_timeout=60)
    at.run()

    page_text = _all_text(at)
    exp_labels = _expander_labels(at)

    # 1) No uncaught exception.
    exc_ok = len(at.exception) == 0
    print(f"\n[1] no uncaught exception          : {'OK' if exc_ok else 'FAIL'} "
          f"({len(at.exception)} exceptions)")
    if not exc_ok:
        for e in at.exception:
            print("      ", getattr(e, "value", e))

    # 2) Scorecard band text renders.
    band_ok = bool(expected_band) and expected_band in page_text
    print(f"[2] scorecard band renders         : {'OK' if band_ok else 'FAIL'} "
          f"(looking for {expected_band!r})")

    # 3) At least one expand-to-source element present.
    exp_ok = len(exp_labels) >= 1
    print(f"[3] expand-to-source present       : {'OK' if exp_ok else 'FAIL'} "
          f"({len(exp_labels)} expanders)")

    # 4) Panel A (synthesis) content renders.
    panelA_ok = (
        "Panel A" in page_text
        and bool(expected_verdict) and expected_verdict in page_text
        and bool(thesis_fragment) and thesis_fragment in page_text
    )
    print(f"[4] Panel A synthesis content      : {'OK' if panelA_ok else 'FAIL'} "
          f"(verdict {expected_verdict!r} + thesis fragment)")

    # 5) At least one claim→figure source expander.
    fig_src = [lbl for lbl in exp_labels if "figure source" in lbl]
    claim_src_ok = len(fig_src) >= 1
    print(f"[5] claim→figure source expander   : {'OK' if claim_src_ok else 'FAIL'} "
          f"({len(fig_src)} figure-source expanders)")

    # 6) A validation_failed / empty panel renders its honest badge (no crash).
    badge_ok = bool(nonok_panels) and _HONEST_BADGE_SUBSTR in page_text
    print(f"[6] honest badge on non-ok panel   : {'OK' if badge_ok else 'FAIL'} "
          f"(badge substring present={_HONEST_BADGE_SUBSTR in page_text})")

    # Inventory (non-blocking).
    print("\n  --- rendered inventory ---")
    for kind in ("title", "header", "subheader", "markdown", "caption", "error", "expander"):
        try:
            print(f"    {kind:<10}: {len(getattr(at, kind))}")
        except Exception:
            pass
    print(f"    figure-source expanders: {len(fig_src)}")

    ok = exc_ok and band_ok and exp_ok and panelA_ok and claim_src_ok and badge_ok
    print("\n" + "=" * 70)
    print("GATE: ALL CHECKS PASSED" if ok else "GATE: FAILED")
    print("=" * 70)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
