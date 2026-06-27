"""
Tag resolver (build_plan.md §6 components 2 & 4).

Turns the raw companyfacts payload into `ResolvedFact`s. The contract (§2/§6):
every call returns a value WITH its tag and confidence tier, or an explicit
`not_found` — never a bare number, never a silent zero, never a wrong tag unlabeled.

Two subtleties beyond "try tags in priority order":

  1. Tag consistency across the multi-year window. If we resolved each year
     independently, a company that switched tags (e.g. `Revenues` ->
     `RevenueFromContractWithCustomerExcludingAssessedTax` around ASC 606) could
     resolve FY2022 via one tag and FY2024 via another — and then YoY, CAGR, and
     the whole trajectory/survival panel would be comparing different concepts.
     Fix: pick the anchor tag from the LATEST year, reuse it for prior years where
     present, and flag any year that had to fall back to a different tag.

  2. Sign assertion. Each concept declares its expected sign (tag_map). If a value
     violates it (e.g. capex tagged negative), we keep the value but attach a loud
     note rather than silently letting it flip FCF/coverage downstream.
"""

from __future__ import annotations

from src.data import dedup, tag_map
from src.data.models import ConfidenceTier, ResolvedFact, make_figure_id


# ---------------------------------------------------------------------------
# dep_amort reconcile (B6b)
# ---------------------------------------------------------------------------
# Two candidates may resolve for the same period with materially different values:
#
#   DepreciationDepletionAndAmortization (DDA) — rank-0; for most filers this IS
#   the cash-flow aggregate.  For others (e.g. CRM FY2024) it is tagged as a
#   PP&E-only sub-component (1,100M) while the cash-flow aggregate sits in
#   DepreciationAndAmortization (DA = 3,959M).
#
#   DepreciationAndAmortization (DA) — rank-2; promoted only after passing all
#   three stages below.  The element name alone is not sufficient evidence.
#
# Stage 2 — bundle guard: tests DA ≈ DDA + CapContractAmort (within 5%).
#   TRUE  → DA IS the trivial PP&E-component + ASC-606-contract-cost sum.
#            Per decision B6b, capitalized-contract-cost amortization is
#            deliberately excluded from the EBITDA D&A add-back (it is a cash
#            working-capital timing item, not a true non-cash depreciation
#            charge); DA is therefore over-inclusive for EBITDA → fall back to
#            DDA, LOW confidence.
#   FALSE → DA ≠ trivial DDA+Cap sum.  Necessary condition that DA is the
#            broader aggregate.  Does NOT prove CapContractAmort is unbundled
#            from DA — only that DA contains more than those two items.
#
# Stage 3 — cash-flow tie: verify XBRL-tagged sub-components of DA
#   (_DA_CF_POOL_TAGS, which excludes CapContractAmort — a separate OCF line;
#   do NOT subtract it from DA) are a consistent subset.
#   Coverage is computed from the CF pool ONLY (independent corroboration);
#   DDA is not counted toward coverage because it is the mislabeled element
#   under test — counting it as its own corroboration would be circular.
#   The CF pool cannot observe acquired-intangible amortization for filers
#   (like CRM post-FY2017) that don't tag AmortizationOfIntangibleAssets;
#   such filers land at LOW confidence — by design.
#
# CRM FY2024 trace:
#   DDA=1,100M  DA=3,959M  CapContractAmort=1,925M  FinLease=264M
#   Stage 1: div=72.2% > 15% → proceeds.
#   Stage 2: DDA+Cap=3,025M vs DA=3,959M → bundle_div=23.6% > 5% → FALSE.
#            (DA ≠ trivial sum — necessary, not sufficient, evidence of aggregation.)
#   Stage 3: cf_pool_sum=264M (FinLease only; AmortIntangibles untagged post-FY2017).
#            coverage = cf_pool_sum/DA = 264M/3,959M = 6.7% < 20% → tie FAILS.
#            DA promoted at LOW.  CapContractAmort(1,925M) is a separate OCF
#            line — do NOT subtract it from DA.
_DA_RECONCILE_THRESHOLD = 0.15   # rel. divergence above which promotion analysis runs
_DA_BUNDLE_GUARD_TOL   = 0.05   # DA ≈ DDA+Cap within 5% → DA bundles the contract line
_DA_CF_MIN_COVERAGE    = 0.20   # independent CF sub-components must cover ≥ 20% of DA
_DA_AGGREGATE_TAG    = "DepreciationAndAmortization"
_DA_COMPONENT_TAG    = "DepreciationDepletionAndAmortization"
_DA_CAP_CONTRACT_TAG = "CapitalizedContractCostAmortization"
# Sub-components summed for the CF tie (CapContractAmort excluded —
# separate OCF line, not a sub-component of the DA aggregate).
_DA_CF_POOL_TAGS = [
    "FinanceLeaseRightOfUseAssetAmortization",
    "AmortizationOfIntangibleAssets",
    "OtherDepreciationAndAmortization",
    "Depreciation",
]


def _da_reconcile_anchor(
    per_tag: dict,
    latest_year: int,
    cap_contract_val: float | None,
    cf_pool_sum: float,      # sum of _DA_CF_POOL_TAGS for anchor year (no CapContractAmort)
) -> tuple[str | None, bool, list[str]]:
    """
    When both DDA and DA resolve for the anchor year, choose the cash-flow aggregate.

    Returns (override_anchor, force_low, notes):
      None, False  ->  keep rank-0 DDA (no promotion warranted).
      DA,   False  ->  promote DA, HIGH confidence (all three stages pass).
      None, True   ->  retain DDA, LOW confidence (bundle guard tripped).
      DA,   True   ->  promote DA, LOW confidence (CF tie failed).
    """
    dda_entry = per_tag.get(_DA_COMPONENT_TAG)
    da_entry  = per_tag.get(_DA_AGGREGATE_TAG)
    if dda_entry is None or da_entry is None:
        return None, False, []

    dda_pick = dda_entry[0].get(latest_year)
    da_pick  = da_entry[0].get(latest_year)
    if dda_pick is None or da_pick is None:
        return None, False, []

    dda_v = dda_pick.fact.get("val")
    da_v  = da_pick.fact.get("val")
    if dda_v is None or da_v is None or dda_v == da_v:
        return None, False, []

    # Stage 1: divergence guard.
    rel_div = abs(da_v - dda_v) / max(abs(da_v), abs(dda_v), 1.0)
    if rel_div <= _DA_RECONCILE_THRESHOLD:
        return None, False, [
            f"dep_amort reconcile FY{latest_year}: DDA={dda_v:,.0f} ≈ DA={da_v:,.0f} "
            f"(div={rel_div:.1%} ≤ {_DA_RECONCILE_THRESHOLD:.0%}); DDA retained "
            f"(scope: component≈aggregate)"
        ]

    # Stage 2: bundle guard — tests whether DA ≈ DDA + CapContractAmort.
    # TRUE means DA IS the trivial PP&E-component + contract-cost sum.  Per
    # decision B6b, ASC-606 capitalized-contract-cost amortization is deliberately
    # excluded from the EBITDA D&A add-back; retaining DDA enforces that exclusion
    # when DA bundles the separately-tagged contract-cost line.
    if cap_contract_val is not None and cap_contract_val > 0:
        bundled = dda_v + cap_contract_val
        bundle_div = abs(da_v - bundled) / max(abs(da_v), abs(bundled), 1.0)
        if bundle_div <= _DA_BUNDLE_GUARD_TOL:
            return None, True, [
                f"dep_amort reconcile FY{latest_year}: DA={da_v:,.0f} ≈ "
                f"DDA+CapContractAmort={bundled:,.0f} (div={bundle_div:.1%} ≤ 5%); "
                f"DA is the trivial PP&E-component + ASC-606-contract-cost sum — "
                f"DDA={dda_v:,.0f} retained (scope: component); confidence LOW. "
                f"Decision B6b: capitalized-contract-cost amortization deliberately "
                f"excluded from EBITDA D&A; retaining DDA enforces that exclusion."
            ]

    # Stage 3: cash-flow tie.
    # known_sum = DDA + cf_pool_sum: used ONLY for the overflow check (a).
    # coverage  = cf_pool_sum / DA:  independent corroboration only.  DDA is
    #   excluded from coverage because it is the mislabeled element under test;
    #   counting it toward its own corroboration would be circular.
    # The CF pool cannot observe acquired-intangible amortization for filers
    # (like CRM post-FY2017) that don't tag AmortizationOfIntangibleAssets;
    # such filers land at LOW confidence — by design.
    known_sum = dda_v + cf_pool_sum
    coverage  = cf_pool_sum / da_v if da_v > 0 else 0.0
    if known_sum > da_v * 1.001:   # rounding epsilon only; a subset exceeding its
        return _DA_AGGREGATE_TAG, True, [  # aggregate is a contradiction, not noise
            f"dep_amort reconcile FY{latest_year}: CF tie FAILED — "
            f"tagged sub-components known_sum={known_sum:,.0f} > DA={da_v:,.0f}; "
            f"DA under-counts its own tagged components; DA promoted at LOW confidence"
        ]
    if coverage < _DA_CF_MIN_COVERAGE:
        return _DA_AGGREGATE_TAG, True, [
            f"dep_amort reconcile FY{latest_year}: CF tie FAILED — "
            f"independent CF pool covers only {coverage:.1%} of DA={da_v:,.0f} "
            f"(cf_pool_sum={cf_pool_sum:,.0f}; threshold {_DA_CF_MIN_COVERAGE:.0%}); "
            f"AmortizationOfIntangibleAssets not tagged for this filer/year — "
            f"element name alone insufficient; DA promoted at LOW confidence"
        ]

    return _DA_AGGREGATE_TAG, False, [
        f"dep_amort reconcile FY{latest_year}: DA={da_v:,.0f} > DDA={dda_v:,.0f} "
        f"(div={rel_div:.1%} > {_DA_RECONCILE_THRESHOLD:.0%}); "
        f"bundle guard passed (DA ≠ trivial DDA+Cap sum); "
        f"CF tie OK — independent coverage={coverage:.1%} (cf_pool_sum={cf_pool_sum:,.0f} / DA); "
        f"≥ {_DA_CF_MIN_COVERAGE:.0%}; DA returned (scope: aggregate)"
    ]


def _facts_for_tag(us_gaap: dict, tag: str, unit_pref: str) -> tuple[list[dict], str | None]:
    """
    Return (facts, unit) for a tag, locked to the concept's expected unit bucket.

    companyfacts groups a tag's values by unit ("USD", "shares", "USD/shares", ...).
    We take ONLY the bucket matching the concept. We deliberately do NOT fall back to
    "whatever bucket exists" (B6): a USD concept must never silently consume an EUR/GBP
    value as if it were USD -- we have no FX layer, so that would be a wrong number. If
    the preferred unit is absent the tag yields nothing here, and resolve_series records
    an explicit reason on the not_found.
    """
    node = us_gaap.get(tag)
    if not node:
        return [], None
    units = node.get("units", {})
    if unit_pref in units:
        return units[unit_pref], unit_pref
    return [], None


def _unit_mismatch_reason(us_gaap: dict, concept_def) -> str | None:
    """If a candidate tag exists but only in a non-preferred unit, explain why we abstained."""
    pref = concept_def.unit
    for cand in concept_def.candidates:
        node = us_gaap.get(cand.tag)
        if node:
            unit_keys = list(node.get("units", {}).keys())
            if unit_keys and pref not in unit_keys:
                return f"{cand.tag} reported only in {unit_keys}, not {pref}; abstained (no FX conversion)"
    return None


def _sign_note(concept: str, value: float | None) -> list[str]:
    """Flag a value whose sign contradicts the concept's expected sign (tag_map)."""
    if value is None:
        return []
    sign = tag_map.expected_sign(concept)
    if sign == "positive" and value < 0:
        return [f"sign anomaly: expected positive, got {value:,.0f}"]
    if sign == "negative" and value > 0:
        return [f"sign anomaly: expected negative, got {value:,.0f}"]
    return []


def _not_found(concept: str, year: int, reason: str | None = None) -> ResolvedFact:
    """Build the explicit not-found figure (§6 component 4) instead of returning None/0."""
    return ResolvedFact(
        concept=concept,
        figure_id=make_figure_id(concept, year),
        value=None,
        fiscal_year=year,
        confidence=ConfidenceTier.NOT_FOUND,
        notes=[reason or "no candidate tag returned a value for this year"],
    )


def resolve_series(
    us_gaap: dict, concept: str, years: list[int], fye_month: int | None,
    label_map: dict | None = None,
) -> dict[int, ResolvedFact]:
    """
    Resolve one concept for several fiscal years at once, enforcing a single tag.

    Returns {year: ResolvedFact} for every requested year (not-found years included).
    `label_map` (period_end -> filer's own fy) makes the year labels per-filer-correct.
    """
    concept_def = tag_map.get_concept(concept)
    unit_pref = concept_def.unit

    # Step 1: for each candidate tag, pre-compute its clean pick per fiscal year.
    #   per_tag[tag] = ({year: PeriodPick}, unit, rank)
    per_tag: dict[str, tuple[dict[int, dedup.PeriodPick], str | None, int]] = {}
    for rank, cand in enumerate(concept_def.candidates):
        facts, used_unit = _facts_for_tag(us_gaap, cand.tag, unit_pref)
        if not facts:
            continue
        by_year = dedup.annual_facts_by_year(facts, concept_def.is_flow, fye_month, label_map)
        if by_year:
            per_tag[cand.tag] = (by_year, used_unit, rank)

    if not per_tag:
        # Concept entirely absent (or present only in a non-preferred unit) for this
        # filer -> every year is not-found, with a unit reason when that's the cause.
        reason = _unit_mismatch_reason(us_gaap, concept_def)
        return {y: _not_found(concept, y, reason) for y in years}

    # dep_amort reconcile: when both DDA and DA are present, may promote DA
    # as the cash-flow aggregate after the three-stage guard.
    _rcn_override: str | None = None
    _rcn_force_low: bool = False
    _rcn_notes: list[str] = []
    if concept == "dep_amort":
        _rcn_latest = max(years)
        # CapContractAmort: for the bundle guard only.  NOT added to the CF pool
        # (separate OCF line; do NOT subtract it from DA).
        _cap_facts, _ = _facts_for_tag(us_gaap, _DA_CAP_CONTRACT_TAG, unit_pref)
        _cap_by_yr = (
            dedup.annual_facts_by_year(_cap_facts, True, fye_month, label_map)
            if _cap_facts else {}
        )
        _cap_pick = _cap_by_yr.get(_rcn_latest)
        _cap_v = _cap_pick.fact.get("val") if _cap_pick else None
        # CF pool: tagged sub-components of the DA aggregate (excluding CapContractAmort).
        _cf_pool_sum = 0.0
        for _pool_tag in _DA_CF_POOL_TAGS:
            _pf, _ = _facts_for_tag(us_gaap, _pool_tag, unit_pref)
            if _pf:
                _pby = dedup.annual_facts_by_year(_pf, True, fye_month, label_map)
                _pp = _pby.get(_rcn_latest)
                if _pp is not None and _pp.fact.get("val") is not None:
                    _cf_pool_sum += float(_pp.fact["val"])
        _rcn_override, _rcn_force_low, _rcn_notes = _da_reconcile_anchor(
            per_tag, _rcn_latest, _cap_v, _cf_pool_sum
        )

    # Step 2: choose the anchor tag from the LATEST requested year, walking candidates
    # in priority order so the most-preferred tag that actually has the latest year wins.
    latest_year = max(years)
    anchor_tag: str | None = None
    if _rcn_override is not None and _rcn_override in per_tag:
        anchor_tag = _rcn_override
    else:
        for cand in concept_def.candidates:
            entry = per_tag.get(cand.tag)
            if entry and latest_year in entry[0]:
                anchor_tag = cand.tag
                break

    # Step 3: resolve each year, preferring the anchor tag, flagging fallbacks.
    out: dict[int, ResolvedFact] = {}
    for year in years:
        chosen_tag: str | None = None

        # Prefer the anchor tag when it has this year (keeps the series on one concept).
        if anchor_tag and year in per_tag[anchor_tag][0]:
            chosen_tag = anchor_tag
        else:
            # Fall back to the highest-priority candidate that has this year.
            for cand in concept_def.candidates:
                entry = per_tag.get(cand.tag)
                if entry and year in entry[0]:
                    chosen_tag = cand.tag
                    break

        if chosen_tag is None:
            out[year] = _not_found(concept, year)
            continue

        by_year, used_unit, rank = per_tag[chosen_tag]
        pick = by_year[year]
        f = pick.fact
        notes = list(pick.notes)

        # Flag a mid-series tag change: this year used a different tag than the anchor.
        if anchor_tag and chosen_tag != anchor_tag:
            notes.append(
                f"tag changed mid-series: used '{chosen_tag}' (anchor is '{anchor_tag}')"
            )

        value = f.get("val")
        notes += _sign_note(concept, value)

        conf = tag_map.tier_for_rank(rank)
        if _rcn_force_low and conf == ConfidenceTier.HIGH:
            conf = ConfidenceTier.LOW

        out[year] = ResolvedFact(
            concept=concept,
            figure_id=make_figure_id(concept, year),
            value=value,
            unit=used_unit,
            tag=chosen_tag,
            fiscal_year=year,
            period_end=dedup._parse(f.get("end")),
            period_start=dedup._parse(f.get("start")) if concept_def.is_flow else None,
            form=f.get("form"),
            accession=f.get("accn"),
            filed=dedup._parse(f.get("filed")),
            confidence=conf,
            notes=notes + _rcn_notes,
        )

    return out


def resolve(
    us_gaap: dict, concept: str, year: int, fye_month: int | None, label_map: dict | None = None
) -> ResolvedFact:
    """Resolve a single (concept, year). Thin wrapper over `resolve_series`."""
    return resolve_series(us_gaap, concept, [year], fye_month, label_map)[year]
