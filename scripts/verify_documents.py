#!/usr/bin/env python3
"""Step 2 gate: verify 10-K fetch + section split on five demo companies."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, ".")

from src.documents.fetch import fetch_and_split_latest_10k  # noqa: E402
from src.documents.models import FilingDocument              # noqa: E402
from src.pipeline import build_financials                    # noqa: E402

TICKERS = ["MSFT", "VZ", "MCD", "NVDA", "CRM"]

_SECTION_FLOORS: dict[str, int] = {
    "item_1":  500,
    "item_1a": 5000,
    "item_7":  2000,
}

_ANCHORS_PATH = Path("tests/fixtures/document_anchors.json")


def check_ticker(ticker: str, anchors: dict) -> bool:
    print(f"\n{'=' * 60}")
    print(f"  {ticker}")
    print(f"{'=' * 60}")

    fin = build_financials(ticker)
    doc = fetch_and_split_latest_10k(ticker, fin)

    print(f"  filing:  {doc.period_of_report}  accession={doc.accession}")
    print(f"  primary: {doc.primary_doc}")

    ok = True

    # split_quality
    if doc.split_quality != "ok":
        print(f"  split_quality=DEGRADED")
        ok = False
    else:
        print(f"  split_quality=ok")

    # Section floors
    for section, floor in _SECTION_FLOORS.items():
        body = doc.sections.get(section, "")
        length = len(body)
        if length >= floor:
            print(f"  {section}=ok  (len={length:,})")
        else:
            print(f"  {section}=FAIL  (len={length:,}, floor={floor:,})")
            ok = False

    # Debt footnote
    fn = doc.sections.get("debt_footnote", "")
    has_kw = any(kw in fn.lower() for kw in ("matur", "contractual", "principal"))
    if fn and has_kw:
        print(f"  debt_footnote=ok  (len={len(fn):,})")
    else:
        print(f"  debt_footnote=FAIL  (len={len(fn):,}, maturity_kw={has_kw})")
        ok = False

    # TOC false-anchor test — two assertions (SEAM 1)
    toc_trap_ok = True

    # (a) Every core section must start after the TOC region.
    for attr, label in [
        ("item_1_start_offset",  "item_1"),
        ("item_1a_start_offset", "item_1a"),
        ("item_7_start_offset",  "item_7"),
    ]:
        section_off = getattr(doc, attr)
        if section_off <= doc.toc_end_offset:
            print(
                f"  toc_trap=FAIL  {attr}={section_off} "
                f"<= toc_end_offset={doc.toc_end_offset}"
            )
            toc_trap_ok = False
            ok = False

    # (b) Item 1A hard floor — risk factors are never < 5000 chars.
    item_1a_len = len(doc.sections.get("item_1a", ""))
    if item_1a_len <= 5000:
        print(f"  toc_trap=FAIL  item_1a len={item_1a_len:,} <= 5000 hard floor")
        toc_trap_ok = False
        ok = False

    if toc_trap_ok:
        print(
            f"  toc_trap=ok  "
            f"toc_end={doc.toc_end_offset:,}  "
            f"item_1={doc.item_1_start_offset:,}  "
            f"item_1a={doc.item_1a_start_offset:,}  "
            f"item_7={doc.item_7_start_offset:,}"
        )

    # Per-company phrase anchors
    company_anchors = anchors.get(ticker, {})
    anchor_ok = True
    for section, phrases in company_anchors.items():
        body = doc.sections.get(section, "").lower()
        for phrase in phrases:
            if phrase.lower() not in body:
                print(f"  anchors=FAIL  '{phrase}' not in {section}")
                anchor_ok = False
                ok = False
    if anchor_ok and company_anchors:
        print(f"  anchors=ok")

    print(f"  → {'PASS' if ok else 'FAIL'}")
    return ok


def main() -> int:
    if not _ANCHORS_PATH.exists():
        print(f"ERROR: {_ANCHORS_PATH} not found — run from project root")
        return 1

    with open(_ANCHORS_PATH) as f:
        anchors = json.load(f)

    results: dict[str, bool] = {}
    for ticker in TICKERS:
        try:
            results[ticker] = check_ticker(ticker, anchors)
        except Exception as exc:
            print(f"  ERROR: {exc}")
            results[ticker] = False

    print(f"\n{'=' * 60}")
    print("SUMMARY")
    print(f"{'=' * 60}")
    for ticker, passed in results.items():
        print(f"  {ticker}: {'PASS' if passed else 'FAIL'}")

    all_passed = all(results.values())
    print(
        f"\n{'STEP 2 GATE: ALL CHECKS PASSED' if all_passed else 'STEP 2 GATE: SOME CHECKS FAILED'}"
    )
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
