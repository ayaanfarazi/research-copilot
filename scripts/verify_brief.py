#!/usr/bin/env python3
"""Phase 3, Step 1 gate: the brief assembler + disk cache.

Usage:
    python scripts/verify_brief.py MSFT

Two-part proof:
  1. assemble_brief(ticker, use_cache=False) — full rebuild. Assert financials ok,
     all six panels present with a valid status, no exceptions. Print a compact
     summary (fiscal_years, anchor-year scorecard band, figure count, per-panel
     status + violation count).
  2. assemble_brief(ticker, use_cache=True) — must be served from disk with zero
     API calls. Proven by (a) the cache file existing, (b) its mtime being
     unchanged across the second call (no rewrite), and (c) near-instant wall clock.
"""

from __future__ import annotations

import sys
import time

sys.path.insert(0, ".")

from src.brief import Brief, _cache_path, assemble_brief
from src.data.models import make_figure_id

_PANEL_NAMES = [
    "business_summary",
    "risks",
    "revenue_drivers",
    "qoe_candidates",
    "synthesis",
    "addback_adversary",
]
_VALID_STATUSES = {"ok", "validation_failed", "confidence_gap"}


def _band(brief: Brief) -> str:
    fig = brief.fin.figures.get(make_figure_id("credit_band", brief.fiscal_year))
    if fig is None:
        return "n/a (no credit_band figure)"
    label = getattr(fig, "label", None)
    return f"{label} (severity {getattr(fig, 'value', None)})"


def _check_brief(brief: Brief) -> bool:
    ok = True

    if brief.fin.status != "ok":
        print(f"  [FAIL] fin.status={brief.fin.status!r} (expected 'ok')")
        ok = False
    else:
        print(f"  [OK ] fin.status = ok")

    for name in _PANEL_NAMES:
        env = getattr(brief, name)
        if env is None:
            print(f"  [FAIL] panel {name!r} missing")
            ok = False
            continue
        status = env.validation.status
        if status not in _VALID_STATUSES:
            print(f"  [FAIL] panel {name!r} status={status!r} (unexpected)")
            ok = False
            continue
        # panel object's own status must agree with the recorded envelope status
        if getattr(env.panel, "status", None) != status:
            print(f"  [FAIL] panel {name!r} status mismatch: "
                  f"panel={env.panel.status!r} envelope={status!r}")
            ok = False
            continue
        print(f"  [OK ] panel {name:<18} status={status:<18} "
              f"violations={env.validation.violation_count}")

    return ok


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python scripts/verify_brief.py TICKER")
        return 1
    ticker = sys.argv[1].upper()

    print("=" * 70)
    print(f"PHASE 3 STEP 1 GATE — brief assembler  ({ticker})")
    print("=" * 70)

    # --- Part 1: full rebuild (use_cache=False) ---------------------------
    print(f"\n[1] assemble_brief({ticker!r}, use_cache=False) — full rebuild")
    t0 = time.perf_counter()
    try:
        brief = assemble_brief(ticker, use_cache=False)
    except Exception:
        import traceback
        print("  [FAIL] assemble_brief raised:")
        traceback.print_exc()
        return 1
    build_secs = time.perf_counter() - t0
    print(f"      rebuilt in {build_secs:.1f}s\n")

    ok = _check_brief(brief)

    # Compact summary
    print("\n  --- summary ---")
    print(f"  ticker        : {brief.ticker}  ({brief.entity_name})")
    print(f"  fiscal_years  : {brief.fin.fiscal_years}")
    print(f"  anchor year   : FY{brief.fiscal_year}")
    print(f"  scorecard band: {_band(brief)}")
    print(f"  figure count  : {len(brief.fin.figures)}")
    for name in _PANEL_NAMES:
        env = getattr(brief, name)
        if env is not None:
            print(f"    {name:<18} status={env.validation.status:<18} "
                  f"{env.validation.violation_count} violations")

    if not ok:
        print("\nGATE: FAILED (part 1 assertions)")
        return 1

    # Round-trip proof: the whole Brief survives JSON.
    reloaded = Brief.model_validate_json(brief.model_dump_json())
    if reloaded.model_dump_json() != brief.model_dump_json():
        print("\n  [FAIL] Brief did not round-trip through JSON")
        return 1
    print("\n  [OK ] Brief round-trips via model_dump_json / model_validate_json")

    cache_file = _cache_path(ticker, brief.fiscal_year)
    if not cache_file.exists():
        print(f"\n  [FAIL] cache file was not written: {cache_file}")
        return 1
    mtime_after_build = cache_file.stat().st_mtime

    # --- Part 2: cache hit (use_cache=True) -------------------------------
    print(f"\n[2] assemble_brief({ticker!r}, use_cache=True) — must serve from cache")
    t0 = time.perf_counter()
    brief2 = assemble_brief(ticker, use_cache=True)
    cache_secs = time.perf_counter() - t0

    mtime_after_cache = cache_file.stat().st_mtime
    if mtime_after_cache != mtime_after_build:
        print(f"  [FAIL] cache file was rewritten (mtime changed) — not a pure cache read")
        return 1
    # Near-instant relative to the rebuild (and in absolute terms).
    if cache_secs > 2.0:
        print(f"  [FAIL] cache read took {cache_secs:.2f}s (expected near-instant)")
        return 1
    if brief2.model_dump_json() != brief.model_dump_json():
        print("  [FAIL] cached Brief differs from rebuilt Brief")
        return 1

    print(f"  [OK ] served from cache: {cache_file}")
    print(f"  [OK ] cache read in {cache_secs*1000:.1f}ms (rebuild was {build_secs:.1f}s), "
          f"file mtime unchanged → zero API calls")

    print("\n" + "=" * 70)
    print("GATE: ALL CHECKS PASSED")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
