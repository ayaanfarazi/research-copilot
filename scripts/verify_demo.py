"""
Phase 1 verification harness (build_plan.md §11 -- non-negotiable hand-verification).

Proves the deterministic layer is correct on real filers before anything is built on
top of it. Three companies chosen to exercise distinct hazards:

  - MSFT : clean baseline + NET CASH case (June FYE, calendar-of-end matches label).
           Full primitive set asserted against the 10-K, incl. a prior-year comparative
           read from a LATER 10-K (the filing-scoped-`fy` trap).
  - TGT  : Jan/Feb FYE retailer -- exercises fiscal-year LABELING (calendar-of-end is
           off by one), which MSFT/AAPL would never catch.
  - VZ   : genuinely levered non-financial -- the survival panel/scorecard must bite.

Plus four pre-commit spot-checks:
  - financial-issuer detection (JPM -> credit panel degraded, not faked);
  - per-filer FY labeling (WMT labels its Jan year-end the OPPOSITE way from TGT's
    near-identical Feb end -- only the filer's own `fy` gets both right);
  - partial-missing cascade (one EBITDA input present, the other absent -> EBITDA and
    net leverage NOT_FOUND, never zero-substituted);
  - D&A series consistency (MSFT's composed D&A is identical across all years);
  - not-found discipline (empty payload -> explicit NOT_FOUND, never 0).

Run:  python scripts/verify_demo.py
Exit: 0 if every check passes, 1 otherwise.
"""

import sys

sys.path.insert(0, ".")

from src.data import dedup, resolver, tag_map  # noqa: E402
from src.data.models import ConfidenceTier  # noqa: E402
from src.metrics import constructed, ratios  # noqa: E402
from src.metrics._common import FigureStore  # noqa: E402
from src.pipeline import build_financials  # noqa: E402
from src.sec.client import get_company_facts  # noqa: E402
from src.sec.ticker import get_cik  # noqa: E402

_TOL = 1.0  # XBRL values are exact integers; this only absorbs presentation rounding.

# Hand-pulled / payload-confirmed golden values from the actual 10-Ks.
# MSFT fiscal years end June 30; revenue/op income/net income are audited headline
# figures, and FY2022/FY2023 are comparatives read from the FY2024 10-K.
MSFT_GOLDEN = {
    "revenue": {2022: 198_270_000_000, 2023: 211_915_000_000, 2024: 245_122_000_000},
    "operating_income": {2022: 83_383_000_000, 2023: 88_523_000_000, 2024: 109_433_000_000},
    "net_income": {2022: 72_738_000_000, 2023: 72_361_000_000, 2024: 88_136_000_000},
    "operating_cash_flow": {2024: 118_548_000_000},
    "capex": {2024: 44_477_000_000},
    "cash": {2024: 18_315_000_000},
    "short_term_investments": {2024: 57_228_000_000},
    "equity": {2024: 268_477_000_000},
    "debt_total": {2024: 44_937_000_000},
    "debt_noncurrent": {2024: 42_688_000_000},
}

# Revenue values observed directly in the raw companyfacts payloads (structural dump).
# NOTE: these are CIRCULAR oracles -- read from the same payload the resolver reads, so
# they only prove dedup is self-consistent, not that the picked fact is correct. The
# independent golden sets below (hand-pulled from the 10-K PDF) are the real oracle.
TGT_REVENUE = {2024: 106_566_000_000, 2025: 104_780_000_000}  # FY label = our convention
VZ_REVENUE = {2025: 138_191_000_000}

# =====================================================================================
# INDEPENDENT GOLDEN SETS -- HAND-PULL FROM THE ACTUAL 10-K PDF. DO NOT fill from the
# payload (that recreates the circularity). Values are reported in MILLIONS in both
# filings, so enter FULL DOLLARS (e.g. "$25,920" million -> 25_920_000_000). The
# comparison tolerance below absorbs millions-rounding. Replace each None, then re-run.
# =====================================================================================

# VZ (Verizon) FY2024 -- levered oracle.
#   Filing: 10-K, accession 0000732712-25-000006, period of report 2024-12-31.
VZ_GOLDEN_FY = 2024
VZ_GOLDEN = {
    "revenue": 134_788_000_000,             # Consolidated Statements of Income -> "Total operating revenues"
    "operating_income": 28_686_000_000,    # -> "Operating income"
    "net_income": 17_506_000_000,          # -> "Net income attributable to Verizon" (NOT the consolidated incl. noncontrolling line)
    "operating_cash_flow": 36_912_000_000, # Statements of Cash Flows -> "Net cash provided by operating activities"
    "capex": 17_090_000_000,               # Statements of Cash Flows -> "Capital expenditures (including capitalized software)"
    "cash": 4_194_000_000,                # Balance Sheet -> "Cash and cash equivalents"
    "equity": 99_237_000_000,              # Balance Sheet -> "Total equity attributable to Verizon" (exclude noncontrolling interests)
    "debt_current": 22_633_000_000,        # Balance Sheet -> "Debt maturing within one year"
    "debt_noncurrent": 121_381_000_000,     # Balance Sheet -> "Long-term debt"
    "total_debt": 144_014_000_000,          # = debt_current + debt_noncurrent (enter the sum you computed)
}

# MCD (McDonald's) FY2024 -- negative-equity + debt-scope oracle (exercises B2 and B4).
#   Filing: 10-K, accession 0000063908-25-000012, period of report 2024-12-31.
MCD_GOLDEN_FY = 2024
MCD_GOLDEN = {
    "revenue": 25_920_000_000,             # Consolidated Statements of Income -> "Total revenues"
    "operating_income": 11_712_000_000,    # -> "Operating income"
    "net_income": 8_223_000_000,          # -> "Net income"
    "cash": 1_085_000_000,                # Balance Sheet -> "Cash and equivalents"
    "operating_cash_flow": 9_447_000_000, # Statements of Cash Flows -> "Cash provided by operations"
    "capex": 2_775_000_000,               # Statements of Cash Flows -> "Capital expenditures"
    "equity": -3_797_000_000,              # Balance Sheet -> "Total shareholders' equity (deficit)"  (a NEGATIVE number)
    "debt_noncurrent": 38_424_000_000,     # Balance Sheet -> "Long-term debt" (the noncurrent line)
    "debt_current": 0,        # Balance Sheet -> "Current maturities of long-term debt"
    "total_debt": 38_424_000_000,          # = debt_noncurrent + debt_current  (the B4 oracle: MUST include the current portion)
}


class Checks:
    """Tiny pass/fail recorder so every assertion prints a line and rolls into one gate."""

    def __init__(self) -> None:
        self.passed = True

    def check(self, ok: bool, desc: str, detail: str = "") -> None:
        self.passed = self.passed and ok
        print(f"  [{'OK ' if ok else 'FAIL'}] {desc}{('  -- ' + detail) if detail else ''}")


def _num(v) -> str:
    return f"{v:,.0f}" if isinstance(v, (int, float)) else str(v)


def verify_msft(ck: Checks) -> None:
    print("\n[MSFT] clean baseline + net cash + prior-year-comparative dedup")
    cik = get_cik("MSFT")
    us_gaap = get_company_facts(cik)["facts"]["us-gaap"]
    fye = dedup.infer_fye_month(us_gaap)
    ck.check(fye == 6, "FYE month inferred as June (6)", f"got {fye}")

    for concept, by_year in MSFT_GOLDEN.items():
        series = resolver.resolve_series(us_gaap, concept, sorted(by_year), fye)
        for year, golden in by_year.items():
            rf = series[year]
            ok = rf.value is not None and abs(rf.value - golden) <= _TOL
            ck.check(ok, f"{concept} FY{year}", f"{_num(rf.value)} vs {_num(golden)} (tag {rf.tag})")

    # The structural proof: FY2022 revenue must come from a LATER 10-K.
    rev22 = resolver.resolve(us_gaap, "revenue", 2022, fye)
    ck.check(rev22.filed is not None and rev22.filed.year > 2022,
             "FY2022 revenue read from a later 10-K (filing-scoped fy handled)",
             f"filed {rev22.filed}")

    cf = build_financials("MSFT", years=5)
    y = cf.fiscal_years[-1]
    nd = cf.get("net_debt", y)
    ck.check(nd.status == "net_cash" and nd.value is not None and nd.value < 0,
             "net cash carried through (net_debt < 0)", f"net_debt={_num(nd.value)}")
    band = cf.get("credit_band", y)
    ck.check(band.label == "strong", "credit band = strong", f"got {band.label}")


def verify_tgt(ck: Checks) -> None:
    print("\n[TGT] Jan/Feb FYE retailer -- fiscal-year labeling")
    cf = build_financials("TGT", years=5)
    for year, golden in TGT_REVENUE.items():
        rf = cf.get("revenue", year)
        ok = rf is not None and rf.value is not None and abs(rf.value - golden) <= _TOL
        ck.check(ok, f"revenue FY{year}", f"{_num(rf.value if rf else None)} vs {_num(golden)}")

    # The labeling test: the FY2025 period ENDS in calendar 2026, but is labeled FY2025
    # (calendar-of-end would wrongly say 2026). This is the off-by-one MSFT can't catch.
    rev = cf.get("revenue", 2025)
    ck.check(rev is not None and rev.period_end is not None and rev.period_end.year == 2026
             and rev.fiscal_year == 2025,
             "FY2025 period ends in 2026 but is labeled FY2025 (FYE-anchored, not calendar-of-end)",
             f"period_end={rev.period_end if rev else None}")

    y = cf.fiscal_years[-1]
    nd = cf.get("net_debt", y)
    ck.check(nd.value is not None and nd.value > 0, "net debt positive (not net cash)",
             f"net_debt={_num(nd.value)}")
    band = cf.get("credit_band", y)
    ck.check(band.label in ("strong", "adequate", "stretched"),
             "credit band is assessed and not 'distressed'", f"got {band.label}")


def verify_vz(ck: Checks) -> None:
    print("\n[VZ] levered non-financial -- the survival/scorecard must bite")
    cf = build_financials("VZ", years=5)
    y = cf.fiscal_years[-1]
    for year, golden in VZ_REVENUE.items():
        rf = cf.get("revenue", year)
        ok = rf is not None and rf.value is not None and abs(rf.value - golden) <= _TOL
        ck.check(ok, f"revenue FY{year}", f"{_num(rf.value if rf else None)} vs {_num(golden)}")

    nd = cf.get("net_debt", y)
    ck.check(nd.value is not None and nd.value > 50e9, "large positive net debt",
             f"net_debt={_num(nd.value)}")
    lev = cf.get("net_leverage", y)
    ck.check(lev.value is not None and 2.0 <= lev.value <= 4.5, "net leverage in a levered range",
             f"net_leverage={lev.value:.2f}x" if lev.value else "n/a")
    cov = cf.get("interest_coverage", y)
    ck.check(cov.value is not None, "interest coverage computed (tag-switch handled)",
             f"coverage={cov.value:.1f}x" if cov.value else "n/a")
    band = cf.get("credit_band", y)
    ck.check(band.label == "adequate",
             "credit band = adequate (spine-driven: leverage adequate / coverage strong)",
             f"got {band.label}")

    # Spot-check #1: print every dimension and confirm liquidity does NOT bind the band.
    print("    per-dimension tiers (severity 0=strong .. 3=distressed):")
    for d in ("leverage", "coverage", "trajectory", "liquidity"):
        s = cf.get(f"score_{d}", y)
        print(f"      {d:11} sev={s.value} ({s.label})")
    notes_text = " | ".join(band.notes)
    ck.check("liquidity flag-only" in notes_text or "liquidity flag:" in notes_text,
             "band notes name liquidity flag (flag-only rule)", notes_text)
    ck.check("binding" not in notes_text.lower() or "liquidity" not in notes_text.lower(),
             "liquidity is NOT a binding dimension", notes_text)
    ck.check(any("verify refinancing capacity" in n for n in band.notes),
             "liquidity flag surfaces for tight runway", " | ".join(band.notes))
    liq, dc = cf.get("liquidity", y), cf.get("debt_current", y)
    ck.check(liq.value is not None and dc.value is not None,
             "liquidity & current-debt both read from real tags (runway not synthetic)",
             f"liquidity={_num(liq.value)} vs current_debt={_num(dc.value)}")


def verify_financial(ck: Checks) -> None:
    print("\n[JPM] financial issuer -- credit panel degraded, not faked")
    cf = build_financials("JPM", years=3)
    ck.check(cf.is_financial, "detected as financial issuer (SIC)", f"SIC {cf.sic}: {cf.sic_description}")
    band = cf.get("credit_band", cf.fiscal_years[-1])
    ck.check(band.label == "not_applicable_financial",
             "credit band degraded for financial issuer", f"got {band.label}")


def verify_wmt_labeling(ck: Checks) -> None:
    # Spot-check #2: Walmart labels its ~Jan-31 fiscal-year-end the OPPOSITE way from
    # Target's near-identical ~Feb-1 end (WMT period ending 2025-01-31 = "fiscal 2025";
    # TGT period ending 2025-02-01 = "fiscal 2024"). No date heuristic satisfies both --
    # only the filer's own `fy` does. Confirm each company lands on its OWN label.
    print("\n[WMT vs TGT] per-filer fiscal-year label (filer's own designation)")
    wmt = build_financials("WMT", years=5)
    rev25 = wmt.get("revenue", 2025)
    ck.check(
        rev25 is not None and rev25.value is not None
        and rev25.period_end is not None and rev25.period_end.year == 2025
        and rev25.period_end.month == 1 and rev25.fiscal_year == 2025,
        "WMT period ending Jan-2025 labeled FY2025 (Walmart's own designation)",
        f"period_end={rev25.period_end if rev25 else None}, label=FY{rev25.fiscal_year if rev25 else None}",
    )
    tgt = build_financials("TGT", years=5)
    rev_t = tgt.get("revenue", 2024)
    ck.check(
        rev_t is not None and rev_t.period_end is not None and rev_t.period_end.year == 2025
        and rev_t.fiscal_year == 2024,
        "TGT period ending Feb-2025 labeled FY2024 (Target's own designation -- opposite of WMT)",
        f"period_end={rev_t.period_end if rev_t else None}, label=FY{rev_t.fiscal_year if rev_t else None}",
    )


def verify_partial_missing(ck: Checks) -> None:
    # Spot-check #3: ONE EBITDA component present (operating income), the other (D&A)
    # not_found. EBITDA and everything downstream must be NOT_FOUND -- never zero-filled.
    print("\n[partial-missing cascade] one EBITDA input present, the other absent")
    us_gaap = {
        "OperatingIncomeLoss": {
            "units": {"USD": [{
                "val": 1_000, "start": "2023-01-01", "end": "2023-12-31",
                "fy": 2023, "fp": "FY", "form": "10-K", "filed": "2024-02-01", "accn": "x",
            }]}
        }
    }
    year, fye, lm = 2023, 12, {}
    store = FigureStore()
    for concept in tag_map.CONCEPTS:
        for fact in resolver.resolve_series(us_gaap, concept, [year], fye, lm).values():
            store.add(fact)

    op = store.get("operating_income", year)
    ck.check(op is not None and op.value == 1_000, "operating income resolves (the present component)",
             f"value={_num(op.value if op else None)}")

    plan = constructed.da_plan(store, [year])
    eb = constructed.compute_ebitda(store, year, plan)
    ck.check(eb.value is None and eb.status == "not_found",
             "EBITDA -> not_found (no zero substitution for missing D&A)",
             f"value={eb.value}, status={eb.status}")

    constructed.compute_total_debt(store, year)
    constructed.compute_net_debt(store, year)
    ratios.compute_credit_ratios(store, year)
    lev = store.get("net_leverage", year)
    ck.check(lev is not None and lev.value is None,
             "net leverage -> not_found (cascade, no zero substitution)", f"value={lev.value if lev else None}")
    # Belt-and-suspenders: nothing in the cascade silently became 0.0.
    zeros = [n for n in ("ebitda", "net_debt", "net_leverage")
             if (f := store.get(n, year)) is not None and f.value == 0]
    ck.check(not zeros, "no figure in the cascade was zero-substituted", f"zeros={zeros}")


def verify_da_consistency(ck: Checks) -> None:
    # Spot-check #4: MSFT composes D&A (no aggregate tag). Confirm the SAME construction
    # (identical component set) is used in EVERY year of the window -- not composed in
    # some, aggregate in others -- so the EBITDA trajectory isn't a method artifact.
    print("\n[D&A series consistency] MSFT composed-D&A must be uniform across years")
    cf = build_financials("MSFT", years=5)
    component_sets = []
    for y in cf.fiscal_years:
        eb = cf.get("ebitda", y)
        comps = tuple(sorted(r.label for r in eb.breakdown if r.label.startswith("+")))
        component_sets.append((y, comps))
        ck.check(not any("inconsistency" in n for n in eb.notes),
                 f"FY{y} EBITDA carries no D&A method-switch flag", f"notes={eb.notes}")
    distinct = {c for _, c in component_sets}
    ck.check(len(distinct) == 1, "D&A construction identical across all years (no method switch)",
             f"{component_sets}")


def verify_independent_goldens(ck: Checks) -> None:
    """
    Compare the pipeline against values HAND-PULLED from the 10-K PDF (non-circular).

    Skips cleanly (and says so) for any golden set still left as None, so the harness
    stays green until you fill them, then becomes a real oracle for B2/B4.
    """
    for tk, fy, gold in [("VZ", VZ_GOLDEN_FY, VZ_GOLDEN), ("MCD", MCD_GOLDEN_FY, MCD_GOLDEN)]:
        filled = {k: v for k, v in gold.items() if v is not None}
        if not filled:
            print(f"\n[{tk} FY{fy} independent golden] NOT POPULATED -- fill from the 10-K PDF, then re-run (skipped)")
            continue
        print(f"\n[{tk} FY{fy} independent golden -- hand-pulled from 10-K, non-circular]")
        cf = build_financials(tk, years=5)
        for concept, golden in filled.items():
            fig = cf.get(concept, fy)
            v = fig.value if fig else None
            tol = max(_TOL, abs(golden) * 5e-4)  # absorbs millions-rounding from the PDF
            ok = v is not None and abs(v - golden) <= tol
            ck.check(ok, f"{tk} {concept} FY{fy}", f"{_num(v)} vs {_num(golden)}")
        if tk == "MCD":
            roe = cf.get("roe", fy)
            ck.check(roe is not None and roe.status == "not_meaningful",
                     "MCD ROE -> not_meaningful (negative book equity, B2)",
                     f"status={roe.status if roe else None}")


def verify_not_found(ck: Checks) -> None:
    print("\n[not-found discipline]")
    nf = resolver.resolve({}, "revenue", 2024, 6)
    ck.check(nf.value is None and nf.confidence == ConfidenceTier.NOT_FOUND,
             "empty payload -> explicit NOT_FOUND (never 0)", f"value={nf.value}")


def run() -> bool:
    print("Phase 1 verification\n" + "=" * 70)
    ck = Checks()
    verify_msft(ck)
    verify_tgt(ck)
    verify_vz(ck)
    verify_financial(ck)
    verify_wmt_labeling(ck)
    verify_partial_missing(ck)
    verify_da_consistency(ck)
    verify_independent_goldens(ck)
    verify_not_found(ck)
    print("\n" + "=" * 70)
    print("ALL CHECKS PASSED" if ck.passed else "SOME CHECKS FAILED")
    return ck.passed


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
