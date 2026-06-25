#!/usr/bin/env python3
"""Cross-step Phase 2 audit: seam and adversarial checks."""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass

sys.path.insert(0, ".")

from pydantic import BaseModel

from src.data.models import CompanyFinancials, ComputedMetric, ConfidenceTier, make_figure_id
from src.documents.fetch import fetch_and_split_latest_10k
from src.documents.models import FilingDocument
from src.documents.split import split_10k
from src.llm.allowlist import build_enumerated_allowlist
from src.llm.normalize import normalize_numeric_token
from src.llm.schemas.citations import Citation
from src.llm.validator import validate_output, validate_text
from src.metrics.qoe import build_qoe_bridge_from_figures
from src.pipeline import build_financials
from src.sec.filings import FilingNotFoundError, find_10k_filing

TICKERS = ["MSFT", "VZ", "MCD", "NVDA", "CRM"]
SECTION_KEYS = {"item_1", "item_1a", "item_7", "debt_footnote"}


class SamplePanel(BaseModel):
    text: str
    citations: list[Citation] = []


@dataclass
class Check:
    name: str
    passed: bool
    detail: str


checks: list[Check] = []


def record(name: str, passed: bool, detail: str) -> None:
    checks.append(Check(name, passed, detail))
    print(f"[{'PASS' if passed else 'FAIL'}] {name}")
    for line in detail.splitlines():
        print(f"       {line}")


def _fin(total_debt: float | None = None, net_lev: float | None = None) -> CompanyFinancials:
    figs = {}
    year = 2024
    if total_debt is not None:
        figs[make_figure_id("total_debt", year)] = ComputedMetric(
            name="total_debt",
            figure_id=make_figure_id("total_debt", year),
            value=total_debt,
            unit="USD",
            confidence=ConfidenceTier.HIGH,
        )
    if net_lev is not None:
        figs[make_figure_id("net_leverage", year)] = ComputedMetric(
            name="net_leverage",
            figure_id=make_figure_id("net_leverage", year),
            value=net_lev,
            unit="x",
            confidence=ConfidenceTier.HIGH,
        )
    return CompanyFinancials(
        ticker="TEST",
        cik="0",
        entity_name="Test",
        fiscal_years=[year],
        figures=figs,
    )


def _doc(sections: dict[str, str] | None = None) -> FilingDocument:
    return FilingDocument(
        ticker="TEST",
        cik="0",
        accession="0000000000-00-000000",
        filed_date="2025-01-01",
        period_of_report="2024-12-31",
        primary_doc="test.htm",
        sections=sections or {},
    )


def audit_anchor_year_and_sections() -> dict[str, tuple[CompanyFinancials, FilingDocument]]:
    print("\n=== SEAM A/C: documents and anchor years ===")
    built: dict[str, tuple[CompanyFinancials, FilingDocument]] = {}
    for ticker in TICKERS:
        fin = build_financials(ticker)
        doc = fetch_and_split_latest_10k(ticker, fin)
        built[ticker] = (fin, doc)
        anchor_year = fin.fiscal_years[-1]
        filing_year = int(doc.period_of_report[:4])
        record(
            f"SEAM A anchor-year consistency {ticker}",
            anchor_year == filing_year,
            f"spine_anchor_year={anchor_year} filing_period={doc.period_of_report}",
        )

        doc_keys = set(doc.sections.keys())
        refs_ok = []
        for key in sorted(SECTION_KEYS):
            text = doc.sections.get(key, "")
            excerpt = text[:80].strip()
            if not excerpt:
                continue
            panel = SamplePanel(
                text="Grounded claim.",
                citations=[Citation(kind="section", ref=key, excerpt=excerpt)],
            )
            result = validate_output(
                panel,
                build_enumerated_allowlist(fin),
                document=doc,
                mode="strict",
            )
            if result.passed:
                refs_ok.append(key)
        record(
            f"SEAM C section-key contract {ticker}",
            doc_keys == SECTION_KEYS and set(refs_ok) == SECTION_KEYS,
            "document_keys="
            + ",".join(sorted(doc_keys))
            + "\nvalidator_resolvable_refs="
            + ",".join(refs_ok)
            + "\nplanned_schema_file=NOT_PRESENT(src/llm/schemas/citations.py)",
        )
    return built


def audit_allowlist_registration(built: dict[str, tuple[CompanyFinancials, FilingDocument]]) -> None:
    print("\n=== SEAM B: QoE allowlist registration ===")
    for ticker, (fin, _) in built.items():
        year = fin.fiscal_years[-1]
        bridge = build_qoe_bridge_from_figures(ticker, fin.figures, year)
        allowlist = build_enumerated_allowlist(fin)
        adj_ebitda = bridge.adjusted_ebitda
        adj_lev = bridge.adjusted_net_leverage

        ebitda_forms = [
            str(int(adj_ebitda.value)),
            f"${adj_ebitda.value / 1_000_000_000:.0f}B",
        ] if adj_ebitda.value is not None else []
        lev_forms = []
        if adj_lev.value is not None:
            lev_forms = [f"{adj_lev.value:.1f}x", f"{adj_lev.value:.2f}x"]

        ebitda_keys = [normalize_numeric_token(form) for form in ebitda_forms]
        lev_keys = [normalize_numeric_token(form) for form in lev_forms]
        ebitda_ok = bool(ebitda_keys) and all(key in allowlist.keys for key in ebitda_keys if key)
        lev_ok = bool(lev_keys) and all(key in allowlist.keys for key in lev_keys if key)
        ids_ok = (
            adj_ebitda.figure_id in fin.figures
            and adj_lev.figure_id in fin.figures
            and adj_ebitda.figure_id in allowlist.figure_ids
            and adj_lev.figure_id in allowlist.figure_ids
        )
        record(
            f"SEAM B adjusted metrics allowlisted {ticker}",
            ebitda_ok and lev_ok and ids_ok,
            f"adjusted_ebitda_id={adj_ebitda.figure_id} keys={ebitda_keys}"
            f"\nadjusted_net_leverage_id={adj_lev.figure_id} keys={lev_keys}"
            f"\nregistered_ids_ok={ids_ok}",
        )


def audit_validator_negative_cases() -> None:
    print("\n=== Validator adversarial checks ===")
    fabricated = validate_text(
        "Debt is $4.2B",
        build_enumerated_allowlist(_fin(total_debt=3_800_000_000)),
        mode="loose",
    )
    fabricated_strict = validate_text(
        "Debt is $4.2B",
        build_enumerated_allowlist(_fin(total_debt=3_800_000_000)),
        mode="strict",
    )
    record(
        "Validator fabricated number fails both modes",
        not fabricated.passed and not fabricated_strict.passed,
        f"strict_passed={fabricated_strict.passed} loose_passed={fabricated.passed}",
    )

    source_doc = _doc({"item_1a": "Real source sentence. Debt was $144 billion."})
    al = build_enumerated_allowlist(_fin(total_debt=144_000_000_000, net_lev=2.9218198622080322))
    bad_excerpt = SamplePanel(
        text="Risk from competition.",
        citations=[Citation(kind="section", ref="item_1a", excerpt="Debt was $999 billion.")],
    )
    bad_result = validate_output(bad_excerpt, al, document=source_doc, mode="strict")
    record(
        "Validator strict fake excerpt fails verbatim check",
        not bad_result.passed and any(v.reason == "excerpt_not_in_source" for v in bad_result.violations),
        f"passed={bad_result.passed} reasons={[v.reason for v in bad_result.violations]}",
    )

    loose_limit = validate_text("Revenue grew 2.92 last year", al, mode="loose")
    record(
        "Validator known loose-mode limitation remains documented",
        loose_limit.passed,
        "membership-only loose mode passes allowed token in fabricated context; "
        f"passed={loose_limit.passed}",
    )

    fin_144 = _fin(total_debt=144_014_000_000)
    al_144 = build_enumerated_allowlist(fin_144)
    exact_m = validate_text("Debt is 144,014", al_144, mode="loose")
    boundary_b = validate_text("Debt is $144.5B", al_144, mode="loose")
    lev = build_enumerated_allowlist(_fin(net_lev=2.9218198622080322))
    fail_293 = validate_text("Leverage is 2.93", lev, mode="loose")
    bare3 = validate_text("Leverage is 3", build_enumerated_allowlist(_fin(net_lev=2.96)), mode="loose")
    record(
        "Validator boundary set live",
        exact_m.passed and not boundary_b.passed and not fail_293.passed and not bare3.passed,
        f"exact_millions_pass={exact_m.passed} 144.5B_pass={boundary_b.passed} "
        f"2.93_pass={fail_293.passed} bare3_pass={bare3.passed}",
    )


def audit_qoe_missing_addback() -> None:
    print("\n=== QoE missing add-back check ===")
    fin = build_financials("MSFT")
    year = fin.fiscal_years[-1]
    bridge = build_qoe_bridge_from_figures("MSFT", fin.figures, year)
    missing = [m for m in bridge.missing_addbacks if m.category == "restructuring"]
    adj = bridge.adjusted_ebitda
    base = fin.get("ebitda", year)
    addback_sum = sum(line.value for line in bridge.addbacks)
    no_zero_fill = base is not None and adj.value == base.value + addback_sum
    record(
        "QoE missing add-back recorded, not zero-filled",
        bool(missing) and missing[0].reason == "not_found" and no_zero_fill,
        f"ticker=MSFT missing={[m.category + ':' + m.reason for m in missing]}"
        f"\nbase_ebitda={base.value if base else None} addback_sum={addback_sum} "
        f"adjusted_ebitda={adj.value}",
    )


def audit_document_degraded_path() -> None:
    print("\n=== Document degraded path ===")
    long_line = (
        "This sentence is intentionally long enough to satisfy the substantive "
        "follow-through heuristic used for real section headings in the splitter."
    )
    text = "\n".join(
        [
            "Item 1. Business",
            long_line,
            "Item 1A. Risk Factors",
            long_line,
            "Item 7. Management's Discussion and Analysis",
            long_line,
            "Item 8. Financial Statements and Supplementary Data",
            "No debt note appears here.",
        ]
    )
    doc = _doc()
    split_10k(text, doc)
    record(
        "Document degraded branch reachable without debt footnote",
        doc.split_quality == "degraded" and not doc.sections.get("debt_footnote"),
        f"split_quality={doc.split_quality} sections={','.join(sorted(doc.sections.keys()))} "
        f"debt_footnote_len={len(doc.sections.get('debt_footnote', ''))}",
    )


def audit_filing_not_found() -> None:
    print("\n=== FilingNotFoundError path ===")
    submissions = {
        "filings": {
            "recent": {
                "form": ["10-K"],
                "accessionNumber": ["0000000000-24-000001"],
                "reportDate": ["2024-12-31"],
                "primaryDocument": ["test.htm"],
                "filingDate": ["2025-02-01"],
            }
        }
    }
    try:
        find_10k_filing(submissions, 1901, ticker="BOGUS")
    except FilingNotFoundError as exc:
        record(
            "FilingNotFoundError raises for unmatched anchor year",
            True,
            str(exc),
        )
        return
    record(
        "FilingNotFoundError raises for unmatched anchor year",
        False,
        "find_10k_filing silently returned a filing for anchor_year=1901",
    )


def print_summary() -> int:
    print("\n=== AUDIT SUMMARY ===")
    for check in checks:
        print(f"{check.name}: {'PASS' if check.passed else 'FAIL'}")
        if not check.passed:
            print(f"  root_cause_or_detail: {check.detail}")
    failed = [c for c in checks if not c.passed]
    print(f"\nTOTAL: {len(checks) - len(failed)} PASS / {len(failed)} FAIL")
    return 1 if failed else 0


def main() -> int:
    built = audit_anchor_year_and_sections()
    audit_allowlist_registration(built)
    audit_validator_negative_cases()
    audit_qoe_missing_addback()
    audit_document_degraded_path()
    audit_filing_not_found()
    return print_summary()


if __name__ == "__main__":
    sys.exit(main())
