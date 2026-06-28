#!/usr/bin/env python3
"""Step 3 gate: descriptive LLM panels through strict validator.

Section A: synthetic adversarial tests (no API calls).
Section B: real API calls on MSFT, VZ, MCD, NVDA, CRM.

Section A must pass fully before Section B begins.
"""

from __future__ import annotations

import sys
import traceback
from typing import Literal, get_args

sys.path.insert(0, ".")

from src.data.models import CompanyFinancials, ComputedMetric, ConfidenceTier, make_figure_id
from src.documents.models import FilingDocument
from src.llm.allowlist import build_enumerated_allowlist
from src.llm.panels.business import generate_business_summary
from src.llm.panels.qoe_candidates import generate_qoe_candidates
from src.llm.panels.revenue_drivers import generate_revenue_drivers
from src.llm.panels.risks import generate_risks
from src.llm.panels.synthesis import generate_anchored_synthesis
from src.llm.schemas.citations import Citation, Claim
from src.llm.schemas.descriptive import (
    BusinessSummaryPanel,
    QoECandidatesPanel,
    RevenueDriversPanel,
    RisksPanel,
)
from src.llm.schemas.synthesis import AnchoredSynthesisPanel
from src.llm.validator import validate_output
from src.metrics.qoe import build_qoe_bridge_from_figures


TICKERS = ["MSFT", "VZ", "MCD", "NVDA", "CRM"]


# ---------------------------------------------------------------------------
# Helpers shared by Section A
# ---------------------------------------------------------------------------

def _fin(total_debt: float | None = None, net_lev: float | None = None) -> CompanyFinancials:
    figs = {}
    y = 2024
    if total_debt is not None:
        figs[make_figure_id("total_debt", y)] = ComputedMetric(
            name="total_debt", figure_id=make_figure_id("total_debt", y),
            value=total_debt, unit="USD", confidence=ConfidenceTier.HIGH,
        )
    if net_lev is not None:
        figs[make_figure_id("net_leverage", y)] = ComputedMetric(
            name="net_leverage", figure_id=make_figure_id("net_leverage", y),
            value=net_lev, unit="x", confidence=ConfidenceTier.HIGH,
        )
    return CompanyFinancials(ticker="TEST", cik="0", entity_name="Test", fiscal_years=[y], figures=figs)


def _doc(sections: dict[str, str] | None = None) -> FilingDocument:
    return FilingDocument(
        ticker="TEST", cik="0", accession="0000000000-00-000000",
        filed_date="2025-01-01", period_of_report="2024-12-31",
        primary_doc="test.htm", sections=sections or {},
    )


# ---------------------------------------------------------------------------
# Section A: synthetic adversarial tests
# ---------------------------------------------------------------------------

def _run_a_case(name: str, passed: bool, expected: bool, detail: str = "") -> bool:
    ok = passed == expected
    mark = "OK" if ok else "FAIL"
    print(f"  [{mark}] {name}")
    if detail:
        print(f"         {detail}")
    if not ok:
        print(f"         EXPECTED passed={expected}, got passed={passed}")
    return ok


def section_a() -> bool:
    print("\n" + "=" * 70)
    print("SECTION A: Synthetic adversarial tests (no API calls)")
    print("=" * 70)

    # Shared fixtures for Section A
    fin = _fin(total_debt=144_000_000_000, net_lev=2.92)
    al = build_enumerated_allowlist(fin)
    # item_7 contains all excerpts used in tests
    item7_src = (
        "Revenue in 2024 was strong. "
        "Cloud was the primary driver. "
        "Volumes expanded across all segments. "
        "The company incurred a settlement. "
        "Management characterizes this as non-recurring."
    )
    doc = _doc({"item_7": item7_src, "item_1a": "Network risks may reduce margins."})

    all_ok = True

    # A1 — nested excerpt verbatim pass: Claim → Citation with numbers in excerpt
    # Numbers inside a verified verbatim excerpt must be exempt from strict scanning.
    panel_a1 = RevenueDriversPanel(
        drivers=[Claim(
            text="Cloud services drove revenue growth.",
            citations=[Citation(kind="section", ref="item_7",
                                excerpt="Revenue in 2024 was strong.")],
        )],
        segment_commentary=[],
        figure_refs_used=[],
    )
    r_a1 = validate_output(panel_a1, al, document=doc, mode="strict")
    all_ok &= _run_a_case(
        "A1 — nested excerpt verbatim pass (numbers in excerpt are exempt)",
        r_a1.passed, True,
        f"passed={r_a1.passed} violations={[v.reason for v in r_a1.violations]}",
    )

    # A2 — nested excerpt NOT verbatim → excerpt_not_in_source
    panel_a2 = RevenueDriversPanel(
        drivers=[Claim(
            text="Cloud services drove revenue growth.",
            citations=[Citation(kind="section", ref="item_7",
                                excerpt="Revenue in 2025 was strong.")],  # not in source
        )],
        segment_commentary=[],
        figure_refs_used=[],
    )
    r_a2 = validate_output(panel_a2, al, document=doc, mode="strict")
    all_ok &= _run_a_case(
        "A2 — nested excerpt NOT verbatim → excerpt_not_in_source",
        r_a2.passed, False,
        f"passed={r_a2.passed} reasons={[v.reason for v in r_a2.violations]}",
    )
    if not r_a2.passed:
        assert any(v.reason == "excerpt_not_in_source" for v in r_a2.violations), (
            "Expected excerpt_not_in_source in violations"
        )

    # A3 — Panel 3 adversarial: numeric magnitude in Claim.text fails strict
    panel_a3 = RevenueDriversPanel(
        drivers=[Claim(
            text="Revenue grew 16% driven by cloud.",  # "16%" is the violation
            citations=[Citation(kind="section", ref="item_7",
                                excerpt="Cloud was the primary driver.")],
        )],
        segment_commentary=[],
        figure_refs_used=[],
    )
    r_a3 = validate_output(panel_a3, al, document=doc, mode="strict")
    all_ok &= _run_a_case(
        "A3 — Panel 3 adversarial: '16%' in Claim.text fails strict",
        r_a3.passed, False,
        f"passed={r_a3.passed} tokens={[v.raw_token for v in r_a3.violations]}",
    )

    # A4 — Panel 3 directional language + figure citation passes strict (Issue 1 fix)
    panel_a4 = RevenueDriversPanel(
        drivers=[Claim(
            text="Revenue grew, driven by cloud services.",  # directional, no number
            citations=[Citation(kind="figure", ref="revenue:FY2024", excerpt=None)],
        )],
        segment_commentary=[],
        figure_refs_used=["revenue:FY2024"],
    )
    r_a4 = validate_output(panel_a4, al, document=doc, mode="strict")
    all_ok &= _run_a_case(
        "A4 — Panel 3 directional language + figure citation passes strict",
        r_a4.passed, True,
        f"passed={r_a4.passed} violations={[v.reason for v in r_a4.violations]}",
    )

    # A5 — Panel 4 adversarial: quantified candidate fails strict
    panel_a5 = QoECandidatesPanel(
        claimed_one_time_items=[Claim(
            text="The company incurred a $400M litigation settlement.",
            citations=[Citation(kind="section", ref="item_7",
                                excerpt="The company incurred a settlement.")],
        )]
    )
    r_a5 = validate_output(panel_a5, al, document=doc, mode="strict")
    all_ok &= _run_a_case(
        "A5 — Panel 4 adversarial: '$400M' in Claim.text fails strict",
        r_a5.passed, False,
        f"passed={r_a5.passed} tokens={[v.raw_token for v in r_a5.violations]}",
    )

    # A6 — Panel 4 clean unquantified candidate passes strict
    panel_a6 = QoECandidatesPanel(
        claimed_one_time_items=[Claim(
            text="Management characterizes this charge as non-recurring.",
            citations=[Citation(kind="section", ref="item_7",
                                excerpt="The company incurred a settlement.")],
        )]
    )
    r_a6 = validate_output(panel_a6, al, document=doc, mode="strict")
    all_ok &= _run_a_case(
        "A6 — Panel 4 clean unquantified candidate passes strict",
        r_a6.passed, True,
        f"passed={r_a6.passed} violations={[v.reason for v in r_a6.violations]}",
    )

    # A7 — figure citation excerpt=None does not trigger excerpt check
    panel_a7 = RevenueDriversPanel(
        drivers=[Claim(
            text="Leverage remained elevated.",
            citations=[Citation(kind="figure", ref="net_leverage:FY2024", excerpt=None)],
        )],
        segment_commentary=[],
        figure_refs_used=[],
    )
    r_a7 = validate_output(panel_a7, al, document=doc, mode="strict")
    all_ok &= _run_a_case(
        "A7 — figure citation excerpt=None: no false-fail",
        r_a7.passed, True,
        f"passed={r_a7.passed} violations={[v.reason for v in r_a7.violations]}",
    )

    # A8 — figure citation with non-null excerpt → excerpt_not_in_source
    # (figure_ids are not section keys, so lookup returns "")
    panel_a8 = RevenueDriversPanel(
        drivers=[Claim(
            text="Leverage remained elevated.",
            citations=[Citation(kind="figure", ref="net_leverage:FY2024", excerpt="2.9x leverage")],
        )],
        segment_commentary=[],
        figure_refs_used=[],
    )
    r_a8 = validate_output(panel_a8, al, document=doc, mode="strict")
    all_ok &= _run_a_case(
        "A8 — figure citation with non-null excerpt → excerpt_not_in_source",
        r_a8.passed, False,
        f"passed={r_a8.passed} reasons={[v.reason for v in r_a8.violations]}",
    )

    # A9 — year token in Claim.text fails strict
    panel_a9 = RevenueDriversPanel(
        drivers=[Claim(
            text="Revenue in 2024 was strong.",  # "2024" is usd:2024, not in allowlist
            citations=[Citation(kind="section", ref="item_7",
                                excerpt="Cloud was the primary driver.")],
        )],
        segment_commentary=[],
        figure_refs_used=[],
    )
    r_a9 = validate_output(panel_a9, al, document=doc, mode="strict")
    all_ok &= _run_a_case(
        "A9 — year '2024' in Claim.text fails strict",
        r_a9.passed, False,
        f"passed={r_a9.passed} tokens={[v.raw_token for v in r_a9.violations]}",
    )

    # A9b — year in verbatim excerpt passes strict (Issue 2 resolution)
    # Model writes the qualitative statement in text; year is anchored in verified excerpt.
    panel_a9b = RevenueDriversPanel(
        drivers=[Claim(
            text="Revenue growth was strong this fiscal year.",  # no year in text
            citations=[Citation(kind="section", ref="item_7",
                                excerpt="Revenue in 2024 was strong.")],  # year in excerpt
        )],
        segment_commentary=[],
        figure_refs_used=[],
    )
    r_a9b = validate_output(panel_a9b, al, document=doc, mode="strict")
    all_ok &= _run_a_case(
        "A9b — year in verbatim excerpt passes strict (correct year-bearing pattern)",
        r_a9b.passed, True,
        f"passed={r_a9b.passed} violations={[v.reason for v in r_a9b.violations]}",
    )

    # A10 — schema migration: Citation.kind must be Literal["section", "figure"]
    kind_field = Citation.model_fields["kind"]
    kind_annotation = kind_field.annotation
    # For Pydantic v2, Literal types compare equal to themselves
    expected_literal = Literal["section", "figure"]
    migration_ok = (get_args(kind_annotation) == get_args(expected_literal))
    all_ok &= _run_a_case(
        "A10 — Citation.kind is Literal['section', 'figure'] (not bare str)",
        migration_ok, True,
        f"kind.annotation={kind_annotation}",
    )

    # A11 — figure_refs_used with figure_ids passes strict
    # _FIGURE_ID_RE masks "[\w]+:FY20\d{2}" before token extraction → no tokens
    panel_a11 = RevenueDriversPanel(
        drivers=[Claim(
            text="Adjusted leverage improved.",
            citations=[Citation(kind="figure", ref="adjusted_ebitda:FY2024", excerpt=None)],
        )],
        segment_commentary=[],
        figure_refs_used=["revenue:FY2024", "adjusted_ebitda:FY2024"],
    )
    r_a11 = validate_output(panel_a11, al, document=doc, mode="strict")
    all_ok &= _run_a_case(
        "A11 — figure_refs_used=['revenue:FY2024', 'adjusted_ebitda:FY2024'] passes strict",
        r_a11.passed, True,
        f"passed={r_a11.passed} violations={[v.reason for v in r_a11.violations]}",
    )

    print()
    if all_ok:
        print("SECTION A: ALL 11 CHECKS PASSED")
    else:
        print("SECTION A: SOME CHECKS FAILED — fix before running Section B")
    return all_ok


# ---------------------------------------------------------------------------
# Section B: real API gate
# ---------------------------------------------------------------------------

def section_b(tickers: list[str] | None = None, panel_filter: str | None = None) -> bool:
    from config import DEMO_PINS
    from src.documents.fetch import fetch_and_split_latest_10k
    from src.pipeline import build_financials

    run_tickers = tickers if tickers else TICKERS
    print("\n" + "=" * 70)
    label = f"panel={panel_filter}" if panel_filter else "all panels"
    print(f"SECTION B: Real API gate ({', '.join(run_tickers)}, {label})")
    print("=" * 70)

    results: list[tuple[str, str, str, bool]] = []  # (ticker, panel, status, no_crash)

    _DEFAULT_PANELS = [
        ("business_summary", generate_business_summary),
        ("risks", generate_risks),
        ("revenue_drivers", generate_revenue_drivers),
        ("qoe_candidates", generate_qoe_candidates),
    ]
    _SYNTHESIS_PANELS = [
        ("synthesis", generate_anchored_synthesis),
    ]

    for ticker in run_tickers:
        print(f"\n--- {ticker} ---")
        try:
            fin = build_financials(ticker, as_of_fy=DEMO_PINS.get(ticker))
            doc = fetch_and_split_latest_10k(ticker, fin)
            year = fin.fiscal_years[-1]
            # Register QoE bridge figures into fin.figures before building allowlist
            build_qoe_bridge_from_figures(ticker, fin.figures, year)
        except Exception:
            print(f"  [FAIL] {ticker}: financials/document fetch failed")
            traceback.print_exc()
            panel_names = (
                ["synthesis"] if panel_filter == "synthesis"
                else [p for p, _ in _DEFAULT_PANELS]
            )
            for pname in panel_names:
                results.append((ticker, pname, "fetch_error", False))
            continue

        panels_to_run = (
            _SYNTHESIS_PANELS if panel_filter == "synthesis" else _DEFAULT_PANELS
        )

        for pname, gen_fn in panels_to_run:
            try:
                panel, vr = gen_fn(fin, doc, year)
            except Exception:
                print(f"  [FAIL] {pname}: unhandled exception")
                traceback.print_exc()
                results.append((ticker, pname, "exception", False))
                continue

            # Hard-fail conditions
            if not hasattr(panel, "status"):
                print(f"  [FAIL] {pname}: returned object has no status field")
                results.append((ticker, pname, "no_status", False))
                continue

            _valid_statuses = {"ok", "validation_failed", "confidence_gap"}
            if panel.status not in _valid_statuses:
                print(f"  [FAIL] {pname}: status={panel.status!r} (unexpected)")
                results.append((ticker, pname, panel.status, False))
                continue

            # Internal consistency: vr.passed ↔ status
            if vr.passed and panel.status in ("validation_failed", "confidence_gap"):
                print(f"  [FAIL] {pname}: vr.passed=True but status={panel.status!r} (inconsistency)")
                results.append((ticker, pname, "inconsistent", False))
                continue

            # Synthesis: print full JSON + ValidationResult, then continue
            if pname == "synthesis":
                _print_synthesis(ticker, year, panel, vr)
                results.append((ticker, pname, panel.status, True))
                continue

            n_violations = len(vr.violations)
            n_claims = _count_claims(panel)
            mark = "PASS" if panel.status == "ok" else ("WARN" if panel.status == "validation_failed" else "GAP")
            print(f"  [{mark}] {pname}: "
                  f"status={panel.status} violations={n_violations} claims={n_claims}")
            if vr.violations:
                for v in vr.violations:
                    print(f"         field={v.field_path!r} token={v.raw_token!r} "
                          f"canonical={v.canonical!r} reason={v.reason!r}")
            results.append((ticker, pname, panel.status, True))

    # Summary
    print("\n" + "=" * 70)
    print("SECTION B SUMMARY")
    print("=" * 70)
    crashed = [(t, p) for t, p, s, ok in results if not ok]
    warned = [(t, p) for t, p, s, ok in results if ok and s == "validation_failed"]
    passed = [(t, p) for t, p, s, ok in results if ok and s == "ok"]

    print(f"  ok:                {len(passed)}/{len(results)}")
    print(f"  validation_failed: {len(warned)}/{len(results)} (warn — gate still passes)")
    print(f"  crashes/errors:    {len(crashed)}/{len(results)}")

    if warned:
        print("\n  validation_failed panels (review system prompts if systematic):")
        for t, p in warned:
            print(f"    {t} / {p}")

    if crashed:
        print("\n  CRASHED panels (gate failure):")
        for t, p in crashed:
            print(f"    {t} / {p}")

    return len(crashed) == 0


def _print_synthesis(ticker: str, year: int, panel: AnchoredSynthesisPanel, vr: ValidationResult) -> None:
    print(f"\n{'=' * 70}")
    print(f"AnchoredSynthesisPanel JSON  ({ticker} FY{year})")
    print("=" * 70)
    print(panel.model_dump_json(indent=2))
    print(f"\nValidationResult:")
    print(f"  passed    : {vr.passed}")
    print(f"  violations: {len(vr.violations)}")
    for v in vr.violations:
        print(f"    field={v.field_path!r}  token={v.raw_token!r}  reason={v.reason!r}")
    mark = "PASS" if panel.status == "ok" else ("WARN" if panel.status == "validation_failed" else "GAP")
    print(f"\n  [{mark}] synthesis: status={panel.status}")


def _count_claims(panel: object) -> int:
    count = 0
    field_names = type(panel).model_fields if hasattr(type(panel), "model_fields") else {}
    for fname in field_names:
        val = getattr(panel, fname, None)
        if isinstance(val, list):
            count += sum(1 for item in val if hasattr(item, "citations"))
        elif hasattr(val, "citations"):
            count += 1
    return count


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    # Usage:
    #   python scripts/verify_panels.py                       → all tickers, all panels
    #   python scripts/verify_panels.py MSFT                  → MSFT only, all panels
    #   python scripts/verify_panels.py MSFT --panel synthesis → MSFT, synthesis only
    args = list(sys.argv[1:])
    panel_filter: str | None = None
    if "--panel" in args:
        idx = args.index("--panel")
        if idx + 1 >= len(args):
            print("ERROR: --panel requires a panel name (e.g. --panel synthesis)")
            return 1
        panel_filter = args[idx + 1].lower()
        args = args[:idx] + args[idx + 2:]
    _KNOWN_PANELS = {"synthesis", "business_summary", "risks", "revenue_drivers", "qoe_candidates"}
    if panel_filter and panel_filter not in _KNOWN_PANELS:
        print(f"ERROR: unknown panel {panel_filter!r}. Known: {sorted(_KNOWN_PANELS)}")
        return 1

    requested = [t.upper() for t in args]
    unknown = [t for t in requested if t not in TICKERS]
    if unknown:
        print(f"ERROR: unknown ticker(s): {unknown}. Valid: {TICKERS}")
        return 1

    a_ok = section_a()
    if not a_ok:
        print("\nSection A failed — aborting before Section B (real API calls).")
        return 1

    b_ok = section_b(requested or None, panel_filter=panel_filter)

    print("\n" + "=" * 70)
    if a_ok and b_ok:
        print("STEP 3 GATE: ALL CHECKS PASSED")
        return 0
    print("STEP 3 GATE: SOME CHECKS FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
