"""
Deduplication + annual isolation (build_plan.md §5 problems ii & iii).

companyfacts returns *every* fact for a tag across *every* filing: originals,
amendments, and — the big one — prior-year comparatives. A single latest 10-K
re-reports the two or three prior years alongside the current one. Empirically
verified on the live MSFT and Target payloads:

  * The `fy`/`fp` fields are FILING-scoped, not period-scoped. MSFT's FY2024 10-K
    stamps fy=2024 on the periods ending 2022-06-30, 2023-06-30 AND 2024-06-30.
    => `fy` is unusable as a period key. The period's identity is its `end` date
       (plus `start` for flows).

  * For a given period, the same value appears in several filings; the newest
    filing's number should win (a restatement supersedes the original), so we
    select by latest `filed`.

  * The `frame` key is sparse and is NOT reliably on the latest-filed instance, so
    it is only used as a tie-breaker for a true same-(end, filed) collision (the
    rare dimensional-leak case), never as the primary selector.

  * Fiscal-year LABELS don't follow a universal date rule: MSFT (June FYE) labels
    its period ending 2024-06-30 "FY2024" (= calendar year of end), but Target
    (~Feb FYE) labels its period ending 2025-02-01 "fiscal 2024" (calendar year of
    end MINUS one). So we anchor the label to the filer's actual fiscal-year-end.

This module is intentionally free of our Pydantic models — it operates on the raw
fact dicts from companyfacts and hands clean picks to resolver.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

# An annual period must be roughly a year long. 52- and 53-week fiscal calendars
# produce 364- and 371-day years, so we allow a generous band and reject anything
# that is clearly a quarter/half-year that slipped through with fp=FY.
_MIN_ANNUAL_DAYS = 350
_MAX_ANNUAL_DAYS = 380

# Forms that carry audited annual figures.
_ANNUAL_FORMS = ("10-K", "10-K/A")


@dataclass
class PeriodPick:
    """The single fact chosen for one (concept, fiscal_year), plus provenance flags."""

    fact: dict
    notes: list[str] = field(default_factory=list)


def _parse(d: str | None) -> date | None:
    """Parse an EDGAR 'YYYY-MM-DD' string to a date, tolerating None/empty."""
    if not d:
        return None
    return datetime.strptime(d, "%Y-%m-%d").date()


def _is_full_year(start: str | None, end: str | None) -> bool:
    """True if [start, end] spans roughly one fiscal year (guards against sub-year facts)."""
    s, e = _parse(start), _parse(end)
    if s is None or e is None:
        return False
    days = (e - s).days
    return _MIN_ANNUAL_DAYS <= days <= _MAX_ANNUAL_DAYS


def build_fy_label_map(
    us_gaap: dict,
    reference_tags: tuple[str, ...] = (
        "NetIncomeLoss",
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Assets",
    ),
) -> dict[date, int]:
    """
    Map each period-end date to the FILER'S OWN fiscal-year number.

    There is no universal date->label rule: Walmart's period ending 2025-01-31 is the
    company's fiscal 2025, but Target's near-identical 2025-02-01 is the company's
    fiscal 2024. The only correct source is the filer's own designation, which the SEC
    `fy` field carries on the PRIMARY (latest-end) period of each 10-K (the comparatives
    in that same filing inherit the filing's `fy`, so only the primary period is
    authoritative). We scan each accession, take its latest-end annual fact, and record
    period_end -> fy from it.
    """
    primary_by_accn: dict[str, dict] = {}  # accession -> its primary (max-end) annual fact
    for tag in reference_tags:
        node = us_gaap.get(tag)
        if not node:
            continue
        for unit_facts in node.get("units", {}).values():
            for f in unit_facts:
                if f.get("form") not in _ANNUAL_FORMS or f.get("fp") != "FY":
                    continue
                end, fy, accn = f.get("end"), f.get("fy"), f.get("accn")
                if not end or fy is None or accn is None:
                    continue
                cur = primary_by_accn.get(accn)
                if cur is None or end > cur["end"]:
                    primary_by_accn[accn] = {"end": end, "fy": fy}

    label_map: dict[date, int] = {}
    for rec in primary_by_accn.values():
        d = _parse(rec["end"])
        if d is not None:
            # Original + amended filings share a period-end and its fy, so overwrite is safe.
            label_map[d] = rec["fy"]
    return label_map


def fiscal_year_label(
    period_end: date, fye_month: int | None = None, label_map: dict[date, int] | None = None
) -> int:
    """
    Map a period-end date to a fiscal-year label.

    Primary source: `label_map` (the filer's OWN fy designation, per
    `build_fy_label_map`) -- the only per-filer-correct option. It labels Walmart's
    Jan-2025 year FY2025 and Target's Feb-2025 year FY2024, matching each company even
    though their year-ends are days apart.

    Fallback (period not in the map): a documented heuristic anchored to the FYE month.
      - A fiscal year ending in the back half of the calendar year (month >= 6,
        i.e. June..December) is labelled by that calendar year.
      - One ending in the front half (Jan..May) is labelled by the PRIOR calendar
        year.
    This matches the marketing labels of every filer we verified (MSFT June->FY2024,
    AAPL Sept->FY2024, Target Feb-1->"fiscal 2024") and is robust to 52/53-week
    drift because every annual period of a given filer ends in the same month band.

    `fye_month` (the filer's fiscal-year-end month, taken from the latest 10-K) is
    the anchor: in normal data it equals `period_end.month`, so we key off the
    period's own end month, which also handles week-calendar drift between Jan/Feb.
    The label is a DISPLAY/GROUPING key only — all series math keys off `period_end`.
    """
    # Primary: the filer's own designation, when we have it for this exact period-end.
    if label_map is not None and period_end in label_map:
        return label_map[period_end]
    # Fallback heuristic: `fye_month` is accepted for clarity/future-proofing, but the
    # per-period end month is what we threshold on, since it tracks 52/53-week drift.
    return period_end.year if period_end.month >= 6 else period_end.year - 1


def infer_fye_month(us_gaap: dict, reference_tags: tuple[str, ...] = ("Assets", "Liabilities")) -> int | None:
    """
    Infer the filer's fiscal-year-end month from the latest 10-K period end.

    We look at a near-universal instant tag (Assets), find the most recent 10-K
    fiscal period end, and return its month. This anchors `fiscal_year_label` to the
    actual filer instead of assuming a calendar year.
    """
    latest: date | None = None
    for tag in reference_tags:
        node = us_gaap.get(tag)
        if not node:
            continue
        for unit_facts in node.get("units", {}).values():
            for f in unit_facts:
                if f.get("form") in _ANNUAL_FORMS and f.get("fp") == "FY":
                    end = _parse(f.get("end"))
                    if end and (latest is None or end > latest):
                        latest = end
        if latest is not None:
            break
    return latest.month if latest else None


def _pick_one(same_year_facts: list[dict]) -> PeriodPick:
    """
    Collapse all facts that resolved to the same fiscal year into one chosen fact.

    Selection order, each step justified by the observed payload structure:
      1. Latest `filed` wins  -> a restatement/amendment supersedes the original,
         and across filings the newest disclosure of a period is the current view.
      2. If a single filing still yields multiple facts for the period (a true
         same-(end, filed) collision, e.g. a stray dimensional value), prefer the
         one carrying a `frame` key -- SEC only frames the canonical undimensioned
         value for a standard period, so its presence signals "consolidated total".
      3. If it is STILL ambiguous, do not guess: keep the first but attach a loud
         note so the collision surfaces in the figure's provenance/verify report.
    """
    notes: list[str] = []

    # Step 1: keep only the facts from the most recent filing date.
    newest_filed = max(f["filed"] for f in same_year_facts)
    contenders = [f for f in same_year_facts if f["filed"] == newest_filed]

    if len(contenders) == 1:
        return PeriodPick(contenders[0], notes)

    # Step 2: residual same-period collision -> prefer the frame-bearing (canonical) fact.
    framed = [f for f in contenders if f.get("frame")]
    if len(framed) == 1:
        notes.append("same-period collision resolved via frame key (canonical value)")
        return PeriodPick(framed[0], notes)

    # Step 3: genuinely ambiguous -> flag rather than silently pick a possibly-wrong value.
    notes.append(
        f"unresolved same-period collision: {len(contenders)} facts share end/filed; "
        "picked first, NEEDS REVIEW"
    )
    return PeriodPick(contenders[0], notes)


def annual_facts_by_year(
    facts: list[dict], is_flow: bool, fye_month: int | None, label_map: dict[date, int] | None = None
) -> dict[int, PeriodPick]:
    """
    Reduce a tag's raw fact list to one clean pick per fiscal year.

    Steps:
      - keep only annual-form facts (10-K / 10-K/A);
      - for flows, require a full-year duration (drops quarterly facts that carry
        fp=FY); instants have no `start`, so the duration guard is skipped;
      - bucket by the fiscal-year LABEL derived from the period `end` date;
      - within each bucket, choose one fact via `_pick_one`.
    """
    buckets: dict[int, list[dict]] = {}
    for f in facts:
        if f.get("form") not in _ANNUAL_FORMS:
            continue
        end = _parse(f.get("end"))
        if end is None:
            continue
        if is_flow and not _is_full_year(f.get("start"), f.get("end")):
            # A flow that isn't ~a year long is a quarter/half-year, not the annual figure.
            continue
        year = fiscal_year_label(end, fye_month, label_map)
        buckets.setdefault(year, []).append(f)

    return {year: _pick_one(group) for year, group in buckets.items()}


def select_annual(
    facts: list[dict], fiscal_year: int, is_flow: bool, fye_month: int | None,
    label_map: dict[date, int] | None = None,
) -> PeriodPick | None:
    """Convenience: the single pick for one fiscal year, or None if absent."""
    return annual_facts_by_year(facts, is_flow, fye_month, label_map).get(fiscal_year)
