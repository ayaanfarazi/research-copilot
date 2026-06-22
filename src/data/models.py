"""
Data-layer contract (build_plan.md §2, §6).

This module defines the *only* shapes the rest of the system is allowed to pass
around for financial data. The central design rule (§2, the "number boundary")
is that a figure is never a bare float: it always travels with its source tag,
period, confidence tier, and — for derived numbers — its formula and the IDs of
the figures it was built from. That is what lets every number in the final brief
expand to "filing, period, XBRL tag(s), formula," and what lets the LLM layer
reference figures *by ID* instead of re-typing values it could hallucinate.

Two figure shapes:
  - `ResolvedFact`     a single value read straight from XBRL (revenue, cash, ...).
  - `ComputedMetric`   a value our code derived from other figures (EBITDA, leverage).

Both are unioned in `CompanyFinancials.figures`, keyed by a stable `figure_id`.
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field


class ConfidenceTier(str, Enum):
    """
    How much to trust a figure, surfaced as a colour/badge in the UI (§6 component 3).

    VERIFIED  - matched a hand-pulled value from the actual 10-K (demo companies). Green.
    HIGH      - came from a top-3 (by filer frequency) tag. Default, no badge.
    LOW       - came from a rank-4+ fallback tag. Amber "check source".
    NOT_FOUND - no candidate tag returned a value. Red "see filing". Never a silent 0.
    """

    VERIFIED = "verified"
    HIGH = "high"
    LOW = "low"
    NOT_FOUND = "not_found"


# Status of a *computed* figure. A value of None is not enough on its own because
# "we couldn't find the inputs" (not_found) is a very different statement from
# "the inputs exist but the ratio is economically meaningless" (e.g. leverage on
# negative EBITDA) or "this is a real, strong state" (net cash). See §7/§8/§11.
FigureStatus = Literal[
    "ok",
    "not_found",      # an input figure was missing -> cascades to not_found (§6 component 4)
    "not_meaningful",  # math is defined but misleading, e.g. leverage when EBITDA <= 0
    "net_cash",       # net debt < 0: a real, strong state, NOT an error (e.g. MSFT)
    "anomaly",        # a sanity check failed, e.g. capex tagged with the wrong sign
]


def make_figure_id(concept: str, fiscal_year: int) -> str:
    """
    Build the stable ID the reasoning layer (§9) cites, e.g. ``net_leverage:FY2024``.

    Keeping ID construction in one function means the scheme can change later in
    exactly one place rather than being string-formatted ad hoc across the codebase.
    """
    return f"{concept}:FY{fiscal_year}"


class ResolvedFact(BaseModel):
    """A single figure read directly from XBRL for one company and one fiscal year."""

    # `kind` is a discriminator so Pydantic never confuses a fact with a metric when
    # deserialising the `CompanyFinancials.figures` union (their fields overlap).
    kind: Literal["fact"] = "fact"

    concept: str                       # our logical name, e.g. "revenue" (not the XBRL tag)
    figure_id: str
    value: float | None                # None == not found; never coerced to 0
    unit: str | None = None            # "USD", "shares", "USD/shares"
    tag: str | None = None             # the XBRL tag that actually resolved
    fiscal_year: int | None = None     # our derived FY label (see dedup.fiscal_year_label)
    period_end: date | None = None     # canonical period identity (the truth we key on)
    period_start: date | None = None   # present for flow concepts only
    form: str | None = None            # "10-K" / "10-K/A"
    accession: str | None = None       # the filing this value came from
    filed: date | None = None
    confidence: ConfidenceTier = ConfidenceTier.NOT_FOUND
    # Free-form provenance flags, e.g. "tag changed mid-series", "sign anomaly".
    notes: list[str] = Field(default_factory=list)


class BridgeRow(BaseModel):
    """One line of an explicit reconciliation, e.g. a row of the EBITDA bridge (§7)."""

    label: str            # "Operating income", "+ D&A", "= EBITDA"
    figure_id: str | None  # the fact/metric this row draws from, for click-to-source
    value: float | None


class ComputedMetric(BaseModel):
    """A figure our deterministic code derived from one or more other figures."""

    kind: Literal["metric"] = "metric"

    name: str                          # logical name, e.g. "net_leverage"
    figure_id: str
    value: float | None
    status: FigureStatus = "ok"
    # Categorical result for figures whose answer is a class, not a number, e.g. a
    # trajectory ("improving"/"worsening") or a scorecard band ("stretched"). Kept
    # alongside `value` so a metric can carry both (e.g. trajectory label + the
    # numeric change that justifies it).
    label: str | None = None
    unit: str | None = None            # "USD", "x" (a multiple), "%", ...
    formula: str = ""                  # human-readable, e.g. "total_debt / EBITDA"
    component_ids: list[str] = Field(default_factory=list)  # figure_ids this was built from
    confidence: ConfidenceTier = ConfidenceTier.HIGH        # weakest of the components
    breakdown: list[BridgeRow] = Field(default_factory=list)  # reconciliation rows if any
    notes: list[str] = Field(default_factory=list)


# A figure in the brief is either a raw fact or a computed metric. The
# `discriminator` tells Pydantic to switch on the `kind` field, which is both
# faster and safer than letting it guess from overlapping field names.
Figure = Annotated[Union[ResolvedFact, ComputedMetric], Field(discriminator="kind")]


class CompanyFinancials(BaseModel):
    """
    The complete deterministic output of Phase 1 for one company.

    Everything downstream (the metrics layer, the LLM layer in Phase 2, the
    Streamlit UI in Phase 3) consumes this object and nothing else. `figures` is
    keyed by `figure_id` so a reasoning claim like "{fig:net_leverage:FY2024}" can
    be resolved back to its source in O(1).
    """

    ticker: str
    cik: str
    entity_name: str
    fye_month: int | None = None       # filer's fiscal-year-end month (anchors FY labels)
    sic: str | None = None             # SIC industry code (from submissions)
    sic_description: str | None = None
    # Banks/insurers don't fit the industrial credit framing (no OperatingIncomeLoss,
    # "revenue" = net interest income, etc.), so the credit panel is degraded for them.
    is_financial: bool = False
    # Overall coverage status. "ok" means we built a normal panel. The others are
    # explicit graceful-degradation outcomes (never a crash): a filer with no usable
    # us-gaap annual data still returns a CompanyFinancials, just an empty one that
    # says WHY. See pipeline.build_financials.
    status: Literal["ok", "no_usgaap_data", "foreign_filer_20f", "no_annual_periods"] = "ok"
    status_detail: str | None = None
    fiscal_years: list[int] = Field(default_factory=list)  # ascending, e.g. [2020, 2021, ...]
    figures: dict[str, Figure] = Field(default_factory=dict)

    def get(self, concept: str, fiscal_year: int) -> Figure | None:
        """Convenience lookup by concept + year without rebuilding the ID by hand."""
        return self.figures.get(make_figure_id(concept, fiscal_year))
