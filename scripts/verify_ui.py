#!/usr/bin/env python3
"""Phase 3, Step 2 gate: the deterministic Streamlit renderer.

Runs app.py headless via streamlit.testing.v1.AppTest against the cached MSFT
brief (zero API calls — use_cache=True in the app). Asserts:

  1. no uncaught exception (at.exception is empty),
  2. the scorecard band text renders somewhere on the page,
  3. at least one expand-to-source element (st.expander) is present.

Prints a compact inventory and the full result.
"""

from __future__ import annotations

import sys

sys.path.insert(0, ".")

from streamlit.testing.v1 import AppTest

from src.brief import assemble_brief
from src.data.models import make_figure_id


def _all_text(at: AppTest) -> str:
    """Concatenate the text of every text-bearing element for substring checks."""
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


def main() -> int:
    print("=" * 70)
    print("PHASE 3 STEP 2 GATE — deterministic Streamlit renderer (MSFT)")
    print("=" * 70)

    # The band label we expect to render, read straight from the cached brief
    # (zero API calls) so the assertion is not hard-coded to a stale value.
    brief = assemble_brief("MSFT", use_cache=True)
    band = brief.fin.figures.get(make_figure_id("credit_band", brief.fiscal_year))
    expected_band = (band.label or "").upper() if band is not None else ""
    print(f"\ncached MSFT anchor FY{brief.fiscal_year}, band label = {expected_band!r}")

    # Run the app headless (default selectbox = MSFT).
    at = AppTest.from_file("app.py", default_timeout=60)
    at.run()

    # 1) No uncaught exception.
    exc_ok = len(at.exception) == 0
    print(f"\n[1] no uncaught exception       : {'OK' if exc_ok else 'FAIL'} "
          f"({len(at.exception)} exceptions)")
    if not exc_ok:
        for e in at.exception:
            print("      ", getattr(e, "value", e))

    # 2) Scorecard band text renders.
    page_text = _all_text(at)
    band_ok = bool(expected_band) and expected_band in page_text
    print(f"[2] scorecard band text renders : {'OK' if band_ok else 'FAIL'} "
          f"(looking for {expected_band!r})")

    # 3) At least one expand-to-source element present.
    n_expanders = len(at.expander)
    exp_ok = n_expanders >= 1
    print(f"[3] expand-to-source present    : {'OK' if exp_ok else 'FAIL'} "
          f"({n_expanders} expanders)")

    # Inventory (non-blocking, for eyeballing).
    print("\n  --- rendered inventory ---")
    for kind in ("title", "header", "subheader", "markdown", "caption", "error", "expander"):
        try:
            print(f"    {kind:<10}: {len(getattr(at, kind))}")
        except Exception:
            pass

    ok = exc_ok and band_ok and exp_ok
    print("\n" + "=" * 70)
    print("GATE: ALL CHECKS PASSED" if ok else "GATE: FAILED")
    print("=" * 70)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
