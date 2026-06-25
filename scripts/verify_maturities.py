"""
Standalone gate: verify the deterministic maturity-schedule parser on three
demo companies (MCD, VZ, MSFT) pinned to FY2024.

Expected behaviour:
  MCD  — inline schedule found; buckets and sum match hand-verified values;
          reconciles within 5% of XBRL total_debt.
  VZ   — linelist schedule found; reconciles within 5% of XBRL total_debt.
  MSFT — parse returns None (no aggregate schedule disclosed); correct behaviour,
          not a failure; pipeline degrades to debt_current proxy.

Does NOT call pipeline.py or survival.py; does NOT modify any existing file.
Run:  python scripts/verify_maturities.py
Exit: 0 all pass, 1 any fail.
"""

import sys

sys.path.insert(0, ".")

from config import DEMO_PINS                                  # noqa: E402
from src.documents.fetch import fetch_and_split_latest_10k    # noqa: E402
from src.documents.maturities import (                        # noqa: E402
    parse_maturity_schedule,
    reconcile_schedule,
)
from src.pipeline import build_financials                      # noqa: E402

# Hand-verified MCD buckets (in millions) from 10-K footnote (3), FY2024:
#   accession 0000063908-25-000012, period 2024-12-31
EXPECTED_MCD_BUCKETS = {
    "2025": 0,
    "2026": 2_392,
    "2027": 3_036,
    "2028": 7_221,
    "2029": 3_394,
    "thereafter": 22_573,
}
EXPECTED_MCD_SUM = 38_616   # principal before fair-value adjustments and deferred costs


def _total_debt_millions(fin, year: int) -> int | None:
    """Read total_debt from CompanyFinancials.figures, convert to $M."""
    fig = fin.get("total_debt", year)
    if fig is None or fig.value is None:
        return None
    return round(fig.value / 1_000_000)


def check(ok: bool, desc: str, detail: str = "") -> bool:
    print(f"  [{'OK ' if ok else 'FAIL'}] {desc}" + (f"  -- {detail}" if detail else ""))
    return ok


def run() -> bool:
    passed = True
    print("Maturity-schedule parser verification\n" + "=" * 60)

    # (ticker, expect_schedule): True = schedule should be found; False = None expected
    for ticker, expect_schedule in [("MCD", True), ("VZ", True), ("MSFT", False)]:
        print(f"\n--- {ticker} ---")
        fin = build_financials(ticker, as_of_fy=DEMO_PINS.get(ticker))
        doc = fetch_and_split_latest_10k(ticker, fin)
        ft  = doc.sections.get("debt_footnote", "")
        anchor = fin.fiscal_years[-1]

        print(f"  anchor FY: {anchor}  |  footnote len: {len(ft):,} chars")

        sched = parse_maturity_schedule(ft)

        # ── MSFT: no aggregate schedule — None is correct ──────────────────
        if not expect_schedule:
            ok = sched is None
            passed &= check(
                ok,
                f"{ticker}: parse returns None (no aggregate schedule disclosed)",
                "correct" if ok else f"unexpected: layout={sched.layout if sched else '?'}",
            )
            print(f"  {ticker}: no schedule disclosed, will degrade to debt_current proxy")
            continue

        # ── MCD / VZ: schedule must be found ───────────────────────────────
        passed &= check(
            sched is not None,
            f"{ticker}: schedule found",
            f"layout={sched.layout}" if sched else "returned None",
        )
        if sched is None:
            continue

        print(f"  layout  : {sched.layout}")
        print(f"  buckets : {sched.buckets}")

        td_m = _total_debt_millions(fin, anchor)
        if td_m is None:
            passed &= check(False, f"{ticker}: total_debt FY{anchor} not found")
            continue

        print(f"  total_debt FY{anchor}: ${td_m:,}M  (XBRL carrying value)")
        rec = reconcile_schedule(sched, td_m)
        print(f"  {rec.note}")

        passed &= check(
            rec.reconciled,
            f"{ticker}: principal sum reconciles within 5% of carrying value",
            f"gap={rec.gap_pct:.2%}",
        )

        # ── MCD-specific exact-value assertions ────────────────────────────
        if ticker == "MCD":
            passed &= check(
                sched.buckets == EXPECTED_MCD_BUCKETS,
                "MCD buckets exactly match hand-verified values",
                str(sched.buckets),
            )
            passed &= check(
                sum(sched.buckets.values()) == EXPECTED_MCD_SUM,
                f"MCD principal sum = ${EXPECTED_MCD_SUM:,}M",
                f"got {sum(sched.buckets.values()):,}",
            )

    print("\n" + "=" * 60)
    print("ALL CHECKS PASSED" if passed else "SOME CHECKS FAILED")
    return passed


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
