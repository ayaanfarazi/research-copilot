#!/usr/bin/env python3
"""Step 1 gate: adversarial numeric-token validator harness."""

from __future__ import annotations

import sys

sys.path.insert(0, ".")

from pydantic import BaseModel

from src.data.models import CompanyFinancials, ComputedMetric, ConfidenceTier, make_figure_id
from src.documents.models import FilingDocument
from src.llm.allowlist import build_enumerated_allowlist
from src.llm.normalize import normalize_numeric_token
from src.llm import allowlist as allowlist_mod
from src.llm import tokenize as tokenize_mod
from src.llm.validator import validate_output, validate_text


class Citation(BaseModel):
    kind: str
    ref: str
    excerpt: str | None = None


class SamplePanel(BaseModel):
    text: str
    citations: list[Citation] = []


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


def _run_case(name: str, text: str, fin: CompanyFinancials, exp_strict: bool, exp_loose: bool) -> bool:
    al = build_enumerated_allowlist(fin)
    rs = validate_text(text, al, mode="strict").passed
    rl = validate_text(text, al, mode="loose").passed
    ok = (rs == exp_strict) and (rl == exp_loose)
    mark = "OK" if ok else "FAIL"
    print(f"  [{mark}] {name}")
    print(f"         strict={rs} (exp {exp_strict})  loose={rl} (exp {exp_loose})")
    if not ok:
        for mode, got, exp in [("strict", rs, exp_strict), ("loose", rl, exp_loose)]:
            if got != exp:
                r = validate_text(text, al, mode=mode)
                for v in r.violations[:2]:
                    print(f"           {mode} violation: {v.reason} token={v.raw_token!r} key={v.canonical!r}")
    return ok


def main() -> int:
    print("=" * 70)
    print("STEP 1 GATE: numeric-token validator adversarial harness")
    print("=" * 70)

    # Evidence 1: shared normalization function
    print("\n[1] Shared normalization — single function, two call sites")
    print(f"    Function: normalize_numeric_token @ {normalize_numeric_token.__module__}.{normalize_numeric_token.__name__}")
    print(f"    Allowlist call site: {allowlist_mod.__file__} -> normalize_numeric_token(display_form)")
    print(f"    Tokenizer call site: {tokenize_mod.__file__} -> normalize_numeric_token(raw_token)")
    demo = normalize_numeric_token("$144B")
    print(f"    Demo: normalize_numeric_token('$144B') = {demo!r}")

    # Evidence 2: validator matching logic
    print("\n[2] Validator matching logic (validator.py)")
    import inspect, src.llm.validator as vmod
    src = inspect.getsource(vmod._check_numeric_tokens)
    print("    _check_numeric_tokens body:")
    for line in src.splitlines():
        print(f"      {line}")
    assert "abs(" not in src and "tolerance" not in src.lower() and "float(" not in src

    print("\n[3] Adversarial case table")
    all_ok = True

    fin_38 = _fin(total_debt=3_800_000_000)
    all_ok &= _run_case(
        "Fabricated $4.2B (allowlist 3.8B only)",
        "The company has $4.2B in debt",
        fin_38, exp_strict=False, exp_loose=False,
    )

    fin_42 = _fin(total_debt=4_200_000_000)
    all_ok &= _run_case(
        "Close-wrong $4.21B vs allowed $4.2B",
        "Total debt is $4.21 billion",
        fin_42, exp_strict=False, exp_loose=False,
    )

    fin_144 = _fin(total_debt=144_014_000_000)
    all_ok &= _run_case(
        "Enumerated $144B when 144,014M allowed",
        "Total debt stands at $144B",
        fin_144, exp_strict=False, exp_loose=True,
    )

    fin_lev = _fin(net_lev=2.9218198622080322)
    all_ok &= _run_case(
        "Figure-ID only (no numeric prose)",
        "Leverage per net_leverage:FY2024 is elevated",
        fin_lev, exp_strict=True, exp_loose=True,
    )

    all_ok &= _run_case(
        "Item 1A section ref",
        "See Item 1A for risks",
        fin_lev, exp_strict=True, exp_loose=True,
    )

    all_ok &= _run_case(
        "Smuggled year 2024 in prose",
        "Revenue grew strongly in 2024",
        fin_lev, exp_strict=False, exp_loose=False,
    )

    # Loose-mode known limitation: wrong context, correct membership (ratio key, not pct)
    all_ok &= _run_case(
        "Loose: fabricated context, allowed leverage token (by design PASS loose)",
        "Revenue grew 2.92 last year",
        fin_lev, exp_strict=False, exp_loose=True,
    )

    # Boundary cases from approved policy
    fin_144m = _fin(total_debt=144_014_000_000)
    all_ok &= _run_case(
        "Exact millions 144,014 PASS loose (figure is 144,014M)",
        "Total debt is 144,014",
        fin_144m, exp_strict=False, exp_loose=True,
    )

    all_ok &= _run_case(
        "$144.5B FAIL both (real figure 144,014M — rounding boundary)",
        "Debt is $144.5B",
        fin_144m, exp_strict=False, exp_loose=False,
    )

    fin_lev2 = _fin(net_lev=2.9218198622080322)
    all_ok &= _run_case(
        "Ratio 2.92x PASS loose",
        "Net leverage is 2.92x",
        fin_lev2, exp_strict=False, exp_loose=True,
    )

    all_ok &= _run_case(
        "Ratio 2.93 FAIL both (not in allowlist after x-only fix)",
        "Net leverage is 2.93",
        fin_lev2, exp_strict=False, exp_loose=False,
    )

    # Whole-number ratio injection guard: 2.96 rounds to 3.0 at 1-decimal.
    # After the x-only fix, _ratio_display_forms emits "3.0x" not bare "3.0",
    # so the allowlist must NOT contain a usd:3 key, and bare "3" must FAIL loose.
    fin_296 = _fin(net_lev=2.96)
    al_296 = build_enumerated_allowlist(fin_296)
    assert "usd:3" not in al_296.keys, f"usd:3 key injected into allowlist: {al_296.keys}"
    print("  [OK] Whole-number ratio 2.96 -> no usd:3 key in allowlist")
    all_ok &= _run_case(
        "Bare '3' FAIL loose (ratio 2.96 rounds to 3.0, x-only prevents usd:3 injection)",
        "Leverage is 3",
        fin_296, exp_strict=False, exp_loose=False,
    )

    # Excerpt tests
    doc = FilingDocument(
        ticker="TEST",
        cik="0",
        accession="0000000000-00-000000",
        filed_date="2025-01-01",
        period_of_report="2024-12-31",
        primary_doc="test.htm",
        sections={"item_1a": "Network competition may reduce margins. Debt was $144 billion."},
    )
    fin_144b = _fin(total_debt=144_000_000_000)
    al = build_enumerated_allowlist(fin_144b)
    panel_ok = SamplePanel(
        text="Risk from competition.",
        citations=[Citation(kind="section", ref="item_1a", excerpt="Debt was $144 billion.")],
    )
    r = validate_output(panel_ok, al, document=doc, mode="strict")
    print(f"  [{'OK' if r.passed else 'FAIL'}] Excerpt with numbers, verbatim in source (strict)")
    print(f"         passed={r.passed} (exp True)")
    all_ok &= r.passed

    panel_bad = SamplePanel(
        text="Risk from competition.",
        citations=[Citation(kind="section", ref="item_1a", excerpt="Debt was $999 billion.")],
    )
    r2 = validate_output(panel_bad, al, document=doc, mode="strict")
    print(f"  [{'OK' if not r2.passed else 'FAIL'}] Excerpt NOT in source (fake quote)")
    print(f"         passed={r2.passed} (exp False) reason={[v.reason for v in r2.violations]}")
    all_ok &= not r2.passed

    print("\n" + "=" * 70)
    if all_ok:
        print("STEP 1 GATE: ALL CHECKS PASSED")
        return 0
    print("STEP 1 GATE: SOME CHECKS FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
