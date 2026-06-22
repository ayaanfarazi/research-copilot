"""
Concept -> XBRL tag resolution map (build_plan.md §6).

The hard problem this solves: the *same* economic concept is tagged differently
by different filers ("tag variation"). "Revenue" might be
`RevenueFromContractWithCustomerExcludingAssessedTax` at one company and
`Revenues` at another. So for each logical concept we keep a priority-ordered
list of candidate tags; the resolver (resolver.py) tries them in order and takes
the first that returns data, recording which one it used and how much to trust it.

Each candidate also carries metadata the resolver/constructors need:
  - is_flow       : duration concept (income/cash-flow, spans a year) vs instant
                    (balance-sheet snapshot). Drives dedup AND the /frames/ period
                    format (CY{year} vs CY{year}Q4I).
  - unit          : which XBRL unit bucket to read ("USD", "shares", "USD/shares").
  - expected_sign : the value's normal sign, so we can flag mis-tagged data
                    (e.g. capex tagged negative) instead of silently computing a
                    wrong FCF off it.
  - debt scope    : for debt tags only, whether a tag means current-only,
                    noncurrent-only, or a total. This prevents the double-count
                    trap where `LongTermDebt` (a TOTAL for some filers) is added to
                    a current-debt tag. Verified on MSFT FY2024:
                    LongTermDebt=44,937M (total) vs LongTermDebtNoncurrent=42,688M.

Ordering note (D&A): for EBITDA we want the *total* depreciation+amortization
added back, which lives on the cash-flow statement, so the cash-flow aggregate
tags are ranked above the income-statement depreciation line.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Sign = Literal["positive", "negative", "any"]
DebtScope = Literal["current_only", "noncurrent_only", "total"]


@dataclass(frozen=True)
class TagCandidate:
    """One XBRL tag option for a concept, plus debt scope where relevant."""

    tag: str
    scope: DebtScope | None = None  # only set for debt concepts


@dataclass(frozen=True)
class Concept:
    """A logical financial line item and everything needed to resolve it."""

    name: str
    candidates: list[TagCandidate]
    is_flow: bool
    unit: str = "USD"
    expected_sign: Sign = "any"


def _c(*tags: str) -> list[TagCandidate]:
    """Shorthand: turn bare tag strings into TagCandidates (used for non-debt concepts)."""
    return [TagCandidate(t) for t in tags]


# --- The concept catalogue -------------------------------------------------
# Priority order within each list is the §6 seed, VALIDATED empirically by the
# /frames/ filer-count pull (scripts/build_tag_rankings.py -> data/tag_rankings.json:
# e.g. RevenueFromContractWithCustomer... 3,133 filers > Revenues 2,668). We keep the
# seed order rather than auto-reordering by frequency, because debt concepts are
# scope-sensitive (a more-common tag can be the wrong scope) and the order is already
# hand-verified to pass the demo set. The resolver falls back down the list only when
# earlier tags return nothing.
CONCEPTS: dict[str, Concept] = {
    # --- Income statement (flows) ---
    "revenue": Concept(
        "revenue",
        _c(
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "Revenues",
            "SalesRevenueNet",
            "SalesRevenueGoodsNet",
        ),
        is_flow=True,
        expected_sign="positive",
    ),
    "cogs": Concept(
        "cogs",
        _c(
            "CostOfGoodsAndServicesSold",
            "CostOfRevenue",
            "CostOfGoodsSold",
            "CostOfSales",
        ),
        is_flow=True,
        expected_sign="positive",
    ),
    # Operating income and net income can legitimately be negative (a loss), so
    # expected_sign is "any" -- a negative here is data, not an anomaly.
    "operating_income": Concept(
        "operating_income", _c("OperatingIncomeLoss"), is_flow=True, expected_sign="any"
    ),
    "net_income": Concept(
        "net_income", _c("NetIncomeLoss"), is_flow=True, expected_sign="any"
    ),
    # MSFT illustrates the tag-switch hazard: it used `InterestExpense` through
    # FY2024, then `InterestExpenseNonoperating` from FY2025. Both belong here so the
    # series resolves; the resolver flags the mid-series switch automatically.
    "interest_expense": Concept(
        "interest_expense",
        _c(
            "InterestExpense",
            "InterestExpenseNonoperating",
            "InterestAndDebtExpense",
            "InterestExpenseDebt",
        ),
        is_flow=True,
        expected_sign="positive",
    ),
    # D&A for the EBITDA add-back. We want the TOTAL depreciation + amortization,
    # which is a single cash-flow-statement aggregate for many filers -- but NOT for
    # all. `dep_amort` holds only the true aggregate tags; when none is present
    # (e.g. MSFT, which reports Depreciation and AmortizationOfIntangibleAssets
    # separately), constructed.py composes D&A from the component concepts below and
    # shows each as a tagged row in the EBITDA reconciliation. We deliberately do NOT
    # put bare `Depreciation` here, because using depreciation alone silently
    # understates the add-back.
    "dep_amort": Concept(
        "dep_amort",
        _c(
            "DepreciationDepletionAndAmortization",
            "DepreciationAmortizationAndAccretionNet",
            "DepreciationAndAmortization",
        ),
        is_flow=True,
        expected_sign="positive",
    ),
    # Components used only to COMPOSE D&A when no aggregate tag exists.
    "depreciation": Concept(
        "depreciation",
        _c("Depreciation", "DepreciationNonproduction"),
        is_flow=True,
        expected_sign="positive",
    ),
    "amortization_intangibles": Concept(
        "amortization_intangibles",
        _c("AmortizationOfIntangibleAssets"),
        is_flow=True,
        expected_sign="positive",
    ),
    "tax_expense": Concept(
        "tax_expense",
        _c("IncomeTaxExpenseBenefit"),
        is_flow=True,
        expected_sign="any",
    ),
    # --- Cash flow (flows) ---
    "operating_cash_flow": Concept(
        "operating_cash_flow",
        _c(
            "NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
        ),
        is_flow=True,
        expected_sign="any",
    ),
    # Capex is a cash OUTFLOW normally reported as a positive number; FCF = OCF - capex
    # depends on that. A negative here means the filer flipped the sign -> flag it.
    "capex": Concept(
        "capex",
        _c(
            "PaymentsToAcquirePropertyPlantAndEquipment",
            "PaymentsToAcquireProductiveAssets",
            "PaymentsToAcquireOtherProductiveAssets",  # e.g. Verizon's capex line
        ),
        is_flow=True,
        expected_sign="positive",
    ),
    # --- Balance sheet (instants) ---
    # Debt is split into three scope-labelled concepts so the constructor never sums
    # overlapping tags (see constructed.py). Prefer a `total` tag if present, else
    # current_only + noncurrent_only.
    "debt_total": Concept(
        "debt_total",
        [
            TagCandidate("LongTermDebt", scope="total"),
            TagCandidate("DebtLongtermAndShorttermCombinedAmount", scope="total"),
            # Combined debt + capital/finance leases TOTAL (current + noncurrent), e.g.
            # MPLX reports DebtAndCapitalLeaseObligations = 26.0B as the consolidated total.
            TagCandidate("DebtAndCapitalLeaseObligations", scope="total"),
            # Many REITs (e.g. Realty Income post-2016) report consolidated debt under
            # NotesPayable rather than LongTermDebt. Lowest priority -> resolves only when
            # no standard total/noncurrent tag exists, and lands at LOW confidence (rank 3+).
            TagCandidate("NotesPayable", scope="total"),
        ],
        is_flow=False,
        expected_sign="positive",
    ),
    "debt_current": Concept(
        "debt_current",
        [
            TagCandidate("DebtCurrent", scope="current_only"),
            TagCandidate("LongTermDebtCurrent", scope="current_only"),
            # Filers that fold capital/finance leases into the debt line (Target,
            # Verizon) report the current portion under this tag.
            TagCandidate("LongTermDebtAndCapitalLeaseObligationsCurrent", scope="current_only"),
            TagCandidate("ShortTermBorrowings", scope="current_only"),
        ],
        is_flow=False,
        expected_sign="positive",
    ),
    "debt_noncurrent": Concept(
        "debt_noncurrent",
        [
            TagCandidate("LongTermDebtNoncurrent", scope="noncurrent_only"),
            # Filers that fold capital/finance leases into the debt line report the
            # NONCURRENT long-term portion here (e.g. Verizon FY2024 = 121,381M; the
            # current portion is the separate ...Current tag in debt_current). The B4
            # reconciliation confirms noncurrent + current == the total tag, so this
            # isolates noncurrent without double-counting.
            TagCandidate("LongTermDebtAndCapitalLeaseObligations", scope="noncurrent_only"),
        ],
        is_flow=False,
        expected_sign="positive",
    ),
    # Post-ASU-2016-18, many filers (e.g. Target) report only the combined
    # cash + restricted cash tag, so it's included as a fallback. It can slightly
    # overstate pure cash by any restricted portion -- acceptable and standard.
    "cash": Concept(
        "cash",
        _c(
            "CashAndCashEquivalentsAtCarryingValue",
            "CashAndCashEquivalents",
            "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        ),
        is_flow=False,
        expected_sign="positive",
    ),
    "short_term_investments": Concept(
        "short_term_investments",
        _c("ShortTermInvestments", "MarketableSecuritiesCurrent"),
        is_flow=False,
        expected_sign="positive",
    ),
    "assets": Concept("assets", _c("Assets"), is_flow=False, expected_sign="positive"),
    "liabilities": Concept(
        "liabilities", _c("Liabilities"), is_flow=False, expected_sign="positive"
    ),
    # Equity = book equity ATTRIBUTABLE TO THE PARENT (excludes noncontrolling interest),
    # which is the right denominator for ROE. We deliberately keep ONLY the attributable
    # tag here. Many filers (e.g. Verizon) report no `StockholdersEquity` tag at all and
    # only carry the incl-NCI total; for them the pipeline DERIVES attributable equity as
    # `equity_incl_nci - minority_interest` (see pipeline) rather than letting the incl-NCI
    # total leak in and overstate equity.
    "equity": Concept(
        "equity",
        _c("StockholdersEquity"),
        is_flow=False,
        expected_sign="any",  # can be negative for highly levered / deficit equity firms
    ),
    # Total equity INCLUDING noncontrolling interest, and the NCI itself. These exist only
    # to derive attributable equity when `StockholdersEquity` is absent; they are not used
    # directly as the equity denominator anywhere.
    "equity_incl_nci": Concept(
        "equity_incl_nci",
        _c("StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"),
        is_flow=False,
        expected_sign="any",
    ),
    "minority_interest": Concept(
        "minority_interest",
        _c("MinorityInterest"),
        is_flow=False,
        expected_sign="any",  # usually positive; can be negative in rare deficit cases
    ),
    "op_lease_liab_current": Concept(
        "op_lease_liab_current",
        _c("OperatingLeaseLiabilityCurrent"),
        is_flow=False,
        expected_sign="positive",
    ),
    "op_lease_liab_noncurrent": Concept(
        "op_lease_liab_noncurrent",
        _c("OperatingLeaseLiabilityNoncurrent"),
        is_flow=False,
        expected_sign="positive",
    ),
    "finance_lease_liab_current": Concept(
        "finance_lease_liab_current",
        _c(
            "FinanceLeaseLiabilityCurrent",
            "CapitalLeaseObligationsCurrent",
        ),
        is_flow=False,
        expected_sign="positive",
    ),
    "finance_lease_liab_noncurrent": Concept(
        "finance_lease_liab_noncurrent",
        _c(
            "FinanceLeaseLiabilityNoncurrent",
            "CapitalLeaseObligationsNoncurrent",
        ),
        is_flow=False,
        expected_sign="positive",
    ),
    # --- Share counts / per-share ---
    "diluted_shares": Concept(
        "diluted_shares",
        _c("WeightedAverageNumberOfDilutedSharesOutstanding"),
        is_flow=True,            # a weighted-average over the period -> duration
        unit="shares",
        expected_sign="positive",
    ),
    "eps_diluted": Concept(
        "eps_diluted",
        _c("EarningsPerShareDiluted"),
        is_flow=True,
        unit="USD/shares",
        expected_sign="any",
    ),
}


# --- Lookup helpers (thin wrappers so callers never touch CONCEPTS directly) ---

def get_concept(concept: str) -> Concept:
    """Return the Concept definition or raise a clear error for an unknown name."""
    try:
        return CONCEPTS[concept]
    except KeyError as exc:  # surface the typo loudly rather than returning None
        raise KeyError(f"Unknown concept '{concept}'. Known: {sorted(CONCEPTS)}") from exc


def candidates(concept: str) -> list[TagCandidate]:
    return get_concept(concept).candidates


def is_flow(concept: str) -> bool:
    return get_concept(concept).is_flow


def unit(concept: str) -> str:
    return get_concept(concept).unit


def expected_sign(concept: str) -> Sign:
    return get_concept(concept).expected_sign


def scope_of(concept: str, tag: str) -> DebtScope | None:
    """Return the debt scope of a given tag within a concept (None for non-debt)."""
    for cand in candidates(concept):
        if cand.tag == tag:
            return cand.scope
    return None


def tier_for_rank(rank: int):
    """
    Map a candidate's position in its priority list to a confidence tier (§6 component 3).

    Imported lazily to avoid a circular import (models has no deps; this keeps it that way).
    rank 0-2 (top-3 frequency) -> HIGH; rank 4+ -> LOW. VERIFIED is layered on later by
    the verification harness when a value matches a hand-pulled 10-K number.
    """
    from src.data.models import ConfidenceTier

    return ConfidenceTier.HIGH if rank <= 2 else ConfidenceTier.LOW
