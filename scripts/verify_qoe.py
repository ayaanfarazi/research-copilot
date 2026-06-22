#!/usr/bin/env python3
"""Step 2b gate: verify deterministic QoE bridge metrics on demo companies."""

from __future__ import annotations

import math
import sys

sys.path.insert(0, ".")

from src.metrics.qoe import build_qoe_bridge_from_figures  # noqa: E402
from src.pipeline import build_financials                  # noqa: E402

TICKERS = ["MSFT", "VZ", "MCD", "NVDA", "CRM"]
SBC_REQUIRED = {"MSFT", "NVDA", "CRM"}


def check_ticker(ticker: str) -> bool:
    print(f"\n{'=' * 60}")
    print(f"  {ticker}")
    print(f"{'=' * 60}")

    fin = build_financials(ticker)
    year = fin.fiscal_years[-1]
    bridge = build_qoe_bridge_from_figures(ticker, fin.figures, year)

    base_ebitda = fin.get("ebitda", year)
    adjusted_ebitda = fin.get("adjusted_ebitda", year)
    adjusted_net_leverage = fin.get("adjusted_net_leverage", year)

    ok = True
    print(f"  fiscal_year=FY{year}")
    print(f"  base_ebitda={base_ebitda.value if base_ebitda else None}")

    if adjusted_ebitda and adjusted_ebitda.value is not None:
        positive_ok = adjusted_ebitda.value > 0 or (
            base_ebitda is not None
            and base_ebitda.value is not None
            and base_ebitda.value < 0
        )
        if positive_ok:
            print(
                f"  adjusted_ebitda=ok  "
                f"value={adjusted_ebitda.value}  "
                f"figure_id={adjusted_ebitda.figure_id}"
            )
        else:
            print(
                f"  adjusted_ebitda=FAIL  "
                f"value={adjusted_ebitda.value}  "
                f"base_ebitda={base_ebitda.value if base_ebitda else None}"
            )
            ok = False
    else:
        print("  adjusted_ebitda=FAIL  missing")
        ok = False

    lev_value = adjusted_net_leverage.value if adjusted_net_leverage else None
    if lev_value is not None and math.isfinite(lev_value):
        print(
            f"  adjusted_net_leverage=ok  "
            f"value={lev_value}  "
            f"status={adjusted_net_leverage.status}  "
            f"figure_id={adjusted_net_leverage.figure_id}"
        )
    else:
        print(
            f"  adjusted_net_leverage=FAIL  "
            f"value={lev_value}  "
            f"status={adjusted_net_leverage.status if adjusted_net_leverage else None}"
        )
        ok = False

    if bridge.addbacks:
        print("  addbacks:")
        for line in bridge.addbacks:
            tag_ok = bool(line.tag)
            print(
                f"    {line.category}=tag:{line.tag or 'MISSING_TAG'}  "
                f"value={line.value}  "
                f"confidence={line.confidence.value}  "
                f"figure_id={line.figure_id}"
            )
            if not tag_ok:
                ok = False
    else:
        print("  addbacks=none")

    if ticker in SBC_REQUIRED and not any(line.category == "sbc" for line in bridge.addbacks):
        print(f"  sbc_required=FAIL  {ticker} has no SBC add-back line")
        ok = False
    elif ticker in SBC_REQUIRED:
        print(f"  sbc_required=ok")

    if bridge.missing_addbacks:
        print("  missing_addbacks:")
        for miss in bridge.missing_addbacks:
            print(
                f"    {miss.category}=not_found  "
                f"candidate_tags={','.join(miss.candidate_tags)}"
            )

    if bridge.notes:
        print("  notes:")
        for note in bridge.notes:
            print(f"    {note}")

    print(f"  -> {'PASS' if ok else 'FAIL'}")
    return ok


def main() -> int:
    results = {ticker: check_ticker(ticker) for ticker in TICKERS}

    print(f"\n{'=' * 60}")
    print("SUMMARY")
    print(f"{'=' * 60}")
    for ticker, passed in results.items():
        print(f"  {ticker}: {'PASS' if passed else 'FAIL'}")

    all_passed = all(results.values())
    print(
        f"\n{'STEP 2B GATE: ALL CHECKS PASSED' if all_passed else 'STEP 2B GATE: SOME CHECKS FAILED'}"
    )
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
