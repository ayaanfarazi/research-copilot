"""
Phase 1 orchestration (build_plan.md §11).

`build_financials(ticker)` is the single entry point the later phases (LLM layer,
Streamlit UI) consume. It runs the whole deterministic pipeline:

    ticker -> CIK -> companyfacts
           -> resolve every base concept for the last N fiscal years (ResolvedFacts)
           -> construct EBITDA / total debt / net debt
           -> compute operating + credit ratios, FCF, liquidity
           -> survival (trajectory, coverage durability, liquidity runway)
           -> covenant screen
           -> credit scorecard band
           -> CompanyFinancials (every figure keyed by figure_id)

No LLM anywhere (the §2 number boundary). No printing here -- that belongs to the
scripts; this returns a structured object.
"""

from __future__ import annotations

from src.data import dedup, resolver, tag_map
from src.data.models import CompanyFinancials, ComputedMetric, ConfidenceTier
from src.metrics import constructed, covenant, ratios, scorecard, survival
from src.metrics._common import FigureStore, weakest_tier
from src.sec.client import get_company_facts, get_submissions
from src.sec.ticker import get_cik

# Concepts resolved straight from XBRL before any metric is constructed.
_BASE_CONCEPTS = list(tag_map.CONCEPTS.keys())


def _is_financial_sic(sic: str | None) -> bool:
    """
    True for financial issuers (SIC 6000-6499: banks, credit, brokers, insurers).

    These don't fit the industrial credit framing, so we degrade the credit panel
    rather than print leverage/coverage nonsense off mismatched tags.
    """
    if not sic or not sic.isdigit():
        return False
    return 6000 <= int(sic) < 6500


def _is_reit_sic(sic: str | None) -> bool:
    """True for REITs (SIC 6798). Industrial credit framing does not apply."""
    if not sic or not sic.isdigit():
        return False
    return int(sic) == 6798


def _is_partnership(sic: str | None, entity_name: str) -> bool:
    """
    True for MLPs / partnership structures where equity- and ROE-based metrics are weak.

    SIC 4922-4923 (natural-gas transmission / mixed) plus an LP entity-name suffix
    catches ET, EPD, MPLX and similar midstream partnerships.
    """
    if sic and sic.isdigit() and 4922 <= int(sic) <= 4923:
        return True
    name = (entity_name or "").upper().strip()
    return name.endswith(" LP") or " L.P." in name or name.endswith(" L.P.")


def _has_form(us_gaap: dict, forms: tuple[str, ...]) -> bool:
    """True if any fact in the payload was filed on one of `forms` (e.g. 20-F)."""
    for node in us_gaap.values():
        for unit_facts in node.get("units", {}).values():
            for f in unit_facts:
                if f.get("form") in forms:
                    return True
    return False


def _available_fiscal_years(
    us_gaap: dict, fye_month: int | None, n: int, label_map: dict
) -> list[int]:
    """
    Determine the last `n` fiscal years the filer actually reports.

    We union the annual periods found on a few near-universal reference concepts
    (revenue, then assets, then net income as fallbacks) so we don't depend on any
    single tag being present, then keep the most recent `n`.
    """
    found: set[int] = set()
    for concept in ("revenue", "assets", "net_income"):
        cdef = tag_map.get_concept(concept)
        for cand in cdef.candidates:
            facts, _ = resolver._facts_for_tag(us_gaap, cand.tag, cdef.unit)
            if facts:
                found.update(
                    dedup.annual_facts_by_year(facts, cdef.is_flow, fye_month, label_map).keys()
                )
                break  # first present tag for this concept is enough
    return sorted(found)[-n:]


def build_financials(ticker: str, years: int = 5) -> CompanyFinancials:
    """Run the full deterministic pipeline for `ticker` over the last `years` fiscal years."""
    cik = get_cik(ticker)
    facts = get_company_facts(cik)
    us_gaap = facts["facts"].get("us-gaap", {})
    entity_name = facts.get("entityName", ticker)

    # Issuer metadata: SIC (to flag financials) and the reported FYE as a cross-check.
    submissions = get_submissions(cik)
    sic = submissions.get("sic")
    sic_description = submissions.get("sicDescription")
    is_financial = _is_financial_sic(sic)
    is_reit = _is_reit_sic(sic)
    is_partnership = _is_partnership(sic, entity_name)

    fye_month = dedup.infer_fye_month(us_gaap)
    # Per-filer fiscal-year labels from the filer's own `fy` designation (handles the
    # Walmart-vs-Target case where identical year-ends are labelled differently).
    label_map = dedup.build_fy_label_map(us_gaap)
    fiscal_years = _available_fiscal_years(us_gaap, fye_month, years, label_map)

    # Graceful degradation (B1): no usable us-gaap annual periods. Return an explicit
    # status instead of crashing downstream on years[-1]. Two known root causes:
    #   - IFRS/foreign filers have no us-gaap taxonomy at all;
    #   - 20-F filers report annuals on a form dedup excludes (10-K only) for now.
    if not fiscal_years:
        if not us_gaap:
            status, detail = "no_usgaap_data", "no us-gaap facts found (likely an IFRS/foreign filer)"
        elif _has_form(us_gaap, ("20-F", "20-F/A")):
            status, detail = "foreign_filer_20f", "annual figures filed on Form 20-F; not yet supported"
        else:
            status, detail = "no_annual_periods", "no 10-K annual periods found in us-gaap facts"
        return CompanyFinancials(
            ticker=ticker.upper(), cik=cik, entity_name=entity_name, fye_month=fye_month,
            sic=sic, sic_description=sic_description, is_financial=is_financial,
            status=status, status_detail=detail, fiscal_years=[], figures={},
        )

    store = FigureStore()

    # 1) Resolve every base concept across the whole window (one tag per series).
    for concept in _BASE_CONCEPTS:
        series = resolver.resolve_series(us_gaap, concept, fiscal_years, fye_month, label_map)
        for fact in series.values():
            store.add(fact)

    # 1b) Derive attributable equity when the filer reports no `StockholdersEquity` tag
    # (e.g. Verizon): attributable = total equity incl. NCI - minority interest. Without
    # this, equity would silently include noncontrolling interest and overstate the ROE
    # denominator. Only fires when attributable equity is otherwise missing.
    for year in fiscal_years:
        eq = store.get("equity", year)
        if eq is not None and eq.value is not None:
            continue
        incl = store.get("equity_incl_nci", year)
        if incl is None or incl.value is None:
            continue
        nci = store.get("minority_interest", year)
        nci_v = nci.value if (nci is not None and nci.value is not None) else 0.0
        comps = [incl] + ([nci] if (nci is not None and nci.value is not None) else [])
        note = (
            "attributable equity derived = total equity (incl. NCI) - minority interest"
            if nci_v else
            "only incl-NCI equity tag present and no minority-interest tag; treated NCI as 0"
        )
        store.add(ComputedMetric(
            name="equity", figure_id=store.id("equity", year), value=incl.value - nci_v,
            unit="USD", formula="equity_incl_nci - minority_interest",
            component_ids=[c.figure_id for c in comps], confidence=weakest_tier(*comps),
            notes=[note],
        ))

    # Lock the D&A construction method ONCE for the whole window so the EBITDA
    # trajectory isn't a method-switch artifact (aggregate tag in some years,
    # composed-sum in others). See constructed.da_plan.
    da_plan = constructed.da_plan(store, fiscal_years)

    # 2) Per-year constructed figures, then ratios that depend on them.
    for i, year in enumerate(fiscal_years):
        prev_year = fiscal_years[i - 1] if i > 0 else None

        constructed.compute_ebitda(store, year, da_plan)
        constructed.compute_total_debt(store, year, is_reit=is_reit)
        constructed.compute_total_debt_incl_leases(store, year)
        constructed.compute_net_debt(store, year)

        ratios.compute_credit_ratios(store, year)      # EBITDA/debt -> leverage, coverage, FCF, liquidity
        ratios.compute_operating_ratios(store, year, prev_year)

        survival.compute_liquidity_runway(store, year)  # needs liquidity (from credit ratios)
        covenant.compute_covenant_screen(store, year)

    # 3) Cross-year ratios + survival trends (need the full series in the store).
    ratios.compute_revenue_cagr(store, fiscal_years)
    survival.compute_deleveraging_trajectory(store, fiscal_years)
    survival.compute_coverage_durability(store, fiscal_years)

    # 4) Scorecard per year (needs net_leverage, coverage, trajectory, runway).
    for year in fiscal_years:
        band = scorecard.compute_scorecard(store, year)
        # Degrade the credit verdict for financial issuers: the industrial credit
        # framing (EBITDA leverage, interest coverage) doesn't apply to banks/insurers,
        # so we don't present a band off mismatched tags -- we say so explicitly.
        if is_financial:
            band.value = None
            band.status = "not_found"
            band.label = "not_applicable_financial"
            band.confidence = ConfidenceTier.NOT_FOUND
            band.notes = [
                "credit scorecard not applicable to financial issuers "
                f"(SIC {sic}: {sic_description}); industrial leverage/coverage framing does not fit"
            ]
        elif is_reit:
            band.value = None
            band.status = "not_found"
            band.label = "not_applicable_real_estate"
            band.confidence = ConfidenceTier.NOT_FOUND
            band.notes = [
                "credit scorecard not applicable to REITs "
                f"(SIC {sic}: {sic_description}); industrial leverage/coverage framing does not fit"
            ]
        elif is_partnership:
            band.value = None
            band.status = "not_found"
            band.label = "not_applicable_partnership"
            band.confidence = ConfidenceTier.NOT_FOUND
            band.notes = [
                "credit scorecard not applicable to partnership/MLP structures "
                f"(SIC {sic}: {sic_description}; {entity_name}); "
                "leverage/coverage shown for reference but band withheld until partnership panel exists"
            ]

    return CompanyFinancials(
        ticker=ticker.upper(),
        cik=cik,
        entity_name=entity_name,
        fye_month=fye_month,
        sic=sic,
        sic_description=sic_description,
        is_financial=is_financial,
        fiscal_years=fiscal_years,
        figures=store.figures,
    )
