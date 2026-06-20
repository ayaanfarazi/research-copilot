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


def _facts_for_tag(us_gaap: dict, tag: str, unit_pref: str) -> tuple[list[dict], str | None]:
    """
    Return (facts, unit) for a tag, preferring the concept's expected unit bucket.

    companyfacts groups a tag's values by unit ("USD", "shares", "USD/shares", ...).
    We want the bucket matching the concept; if it's missing we fall back to the
    first available bucket so an unusual filer still resolves (the unit is recorded).
    """
    node = us_gaap.get(tag)
    if not node:
        return [], None
    units = node.get("units", {})
    if unit_pref in units:
        return units[unit_pref], unit_pref
    # Fallback: take whatever single unit bucket exists.
    first_unit = next(iter(units), None)
    return (units.get(first_unit, []), first_unit) if first_unit else ([], None)


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


def _not_found(concept: str, year: int) -> ResolvedFact:
    """Build the explicit not-found figure (§6 component 4) instead of returning None/0."""
    return ResolvedFact(
        concept=concept,
        figure_id=make_figure_id(concept, year),
        value=None,
        fiscal_year=year,
        confidence=ConfidenceTier.NOT_FOUND,
        notes=["no candidate tag returned a value for this year"],
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
        # Concept entirely absent for this filer -> every year is not-found.
        return {y: _not_found(concept, y) for y in years}

    # Step 2: choose the anchor tag from the LATEST requested year, walking candidates
    # in priority order so the most-preferred tag that actually has the latest year wins.
    latest_year = max(years)
    anchor_tag: str | None = None
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
            confidence=tag_map.tier_for_rank(rank),
            notes=notes,
        )

    return out


def resolve(
    us_gaap: dict, concept: str, year: int, fye_month: int | None, label_map: dict | None = None
) -> ResolvedFact:
    """Resolve a single (concept, year). Thin wrapper over `resolve_series`."""
    return resolve_series(us_gaap, concept, [year], fye_month, label_map)[year]
