"""
Human-facing formatting + labeling utilities for the credit brief UI.

Everything a reader sees goes through here so the app never leaks a raw float, an
XBRL enum internal, or a figure_id into the default view. The rules are deliberately
small and pure (no Streamlit, no I/O) so they can be unit-tested directly:

  fmt_money        -> "$2,935M" / "$129.4B" / "not found", sign-preserving
  fmt_multiple     -> "44.1×"
  fmt_percent      -> "37.4%"
  fmt_value        -> status-aware value for a figure (net cash / n/m / not found / by unit)
  label_for        -> plain-English concept label ("interest_coverage" -> "Interest coverage")
  confidence_phrase-> plain words for a ConfidenceTier
  fmt_date         -> "Jun 30, 2024"
  sec_filing_url   -> a real EDGAR filing-index URL (or None if unresolvable)

Money scale note: values are shown in whole millions until they reach $10B, then in
billions to one decimal. That keeps interest expense ($2,935M) readable while large
balance-sheet lines and revenue collapse to billions ($129.4B, $245.1B). The $10B
threshold is what makes fmt_money(2_935_000_000) == "$2,935M".
"""

from __future__ import annotations

from datetime import date

from src.data.models import ConfidenceTier

_NOT_FOUND = "not found"

# Below this many dollars we show whole millions; at/above it we switch to billions.
_BILLIONS_THRESHOLD = 1e10


def fmt_money(v: float | None) -> str:
    """Dollar amount as "$2,935M" (millions) or "$129.4B" (>= $10B). None -> "not found".

    The sign is preserved for negatives; every number is rounded (no raw floats).
    """
    if v is None:
        return _NOT_FOUND
    a = abs(v)
    sign = "-" if v < 0 else ""
    if a >= _BILLIONS_THRESHOLD:
        return f"{sign}${a / 1e9:,.1f}B"
    if a >= 1e6:
        return f"{sign}${a / 1e6:,.0f}M"
    if a >= 1e3:
        return f"{sign}${a:,.0f}"
    return f"{sign}${a:,.0f}"


def fmt_multiple(v: float | None) -> str:
    """A multiple as "44.1×" (one decimal, real multiplication sign)."""
    if v is None:
        return _NOT_FOUND
    return f"{v:,.1f}×"


def fmt_percent(v: float | None) -> str:
    """A percentage as "37.4%" (one decimal)."""
    if v is None:
        return _NOT_FOUND
    return f"{v:,.1f}%"


# Non-"ok" computed statuses map to a plain phrase instead of a number.
_STATUS_VALUE = {
    "net_cash": "Net cash",
    "not_meaningful": "n/m",
    "not_found": "not found — see filing",
}


def fmt_value(fig: object) -> str:
    """Status-aware human value for a figure.

    net_cash -> "Net cash", not_meaningful -> "n/m", not_found / no value ->
    "not found — see filing", otherwise formatted by unit (USD -> money, x ->
    multiple, % -> percent). Categorical figures (a severity/band label) return
    their label. Never returns a raw float.
    """
    status = getattr(fig, "status", None)
    if status in _STATUS_VALUE:
        return _STATUS_VALUE[status]

    unit = getattr(fig, "unit", None) or ""
    val = getattr(fig, "value", None)
    label = getattr(fig, "label", None)

    # Categorical result (trajectory / band / severity): the label IS the value.
    if label and (unit == "" or unit.startswith("severity")):
        return label

    if val is None:
        return "not found — see filing"

    if unit == "USD":
        return fmt_money(val)
    if unit == "x":
        return fmt_multiple(val)
    if unit == "%":
        return fmt_percent(val)
    # Unitless / unknown: round to one decimal, never a raw float.
    return f"{val:,.1f}"


# ---------------------------------------------------------------------------
# Plain-English concept labels
# ---------------------------------------------------------------------------
# Known concepts get a curated sentence-case label; anything else falls back to a
# titleized version of the raw concept so a new metric still reads reasonably.
_CONCEPT_LABELS = {
    # Facts
    "revenue": "Revenue",
    "cost_of_revenue": "Cost of revenue",
    "gross_profit": "Gross profit",
    "operating_income": "Operating income",
    "net_income": "Net income",
    "interest_expense": "Interest expense",
    "depreciation": "Depreciation",
    "amortization_intangibles": "Amortization of intangibles",
    "cash": "Cash & equivalents",
    "short_term_investments": "Short-term investments",
    "capex": "Capital expenditure",
    "ocf": "Operating cash flow",
    "operating_cash_flow": "Operating cash flow",
    "cogs": "Cost of revenue",
    "sbc": "Share-based compensation",
    "total_equity": "Total equity",
    "current_debt": "Current debt",
    "debt_total": "Total debt (balance sheet)",
    # Computed metrics. Acronyms stay upper-case ("EBITDA", not "Ebitda"); the
    # "adjusted" qualifier stays lower so it reads naturally mid-sentence.
    "ebitda": "EBITDA",
    "adjusted_ebitda": "adjusted EBITDA",
    "adjusted_net_leverage": "Adjusted net leverage",
    "total_debt": "Total debt",
    "net_debt": "Net debt",
    "total_leverage": "Total leverage",
    "net_leverage": "Net leverage",
    "interest_coverage": "Interest coverage",
    "cash_interest_coverage": "Cash interest coverage",
    "fcf": "Free cash flow",
    "fcf_to_debt": "FCF / total debt",
    "liquidity": "Liquidity",
    "gross_margin": "Gross margin",
    "operating_margin": "Operating margin",
    "net_margin": "Net margin",
    "revenue_yoy": "Revenue YoY growth",
    "revenue_cagr": "Revenue CAGR",
    "roe": "Return on equity",
    "deleveraging_trajectory": "Deleveraging trajectory",
    "coverage_durability": "Coverage durability",
    "liquidity_runway": "Liquidity runway",
    "maturity_wall": "Maturity wall",
    "covenant_leverage": "Covenant leverage screen",
    "covenant_coverage": "Covenant coverage screen",
    "credit_band": "Credit band",
    # Scorecard dimensions (read plainly inside the band's source drill-down).
    "score_leverage": "Leverage",
    "score_coverage": "Coverage",
    "score_trajectory": "Trajectory",
    "score_liquidity": "Liquidity",
}


def label_for(concept: str | None) -> str:
    """Plain-English label for a concept name.

    "interest_coverage" -> "Interest coverage". Unknown concepts titleize their
    underscored form so the UI degrades to something readable rather than a raw id.
    """
    if not concept:
        return "—"
    known = _CONCEPT_LABELS.get(concept)
    if known:
        return known
    return concept.replace("_", " ").title()


# ---------------------------------------------------------------------------
# Confidence, in plain words
# ---------------------------------------------------------------------------
_CONFIDENCE_PHRASE = {
    ConfidenceTier.VERIFIED: "Verified — matched a hand-checked value from the filing",
    ConfidenceTier.HIGH: "High — from the most common XBRL tag for this concept",
    ConfidenceTier.LOW: "Low — from a fallback tag; worth checking against the filing",
    ConfidenceTier.NOT_FOUND: "Not found — no tag resolved; see the filing",
}


def confidence_phrase(tier: object) -> str:
    """Plain-words description of a ConfidenceTier (accepts the enum or its value)."""
    if tier is None:
        return ""
    if isinstance(tier, ConfidenceTier):
        return _CONFIDENCE_PHRASE.get(tier, "")
    # Accept a raw string like "high".
    try:
        return _CONFIDENCE_PHRASE.get(ConfidenceTier(str(tier)), "")
    except ValueError:
        return ""


# ---------------------------------------------------------------------------
# Dates + SEC filing links
# ---------------------------------------------------------------------------

def fmt_date(d: date | None) -> str:
    """A human date, "Jun 30, 2024". None -> "—"."""
    if d is None:
        return "—"
    return f"{d:%b} {d.day}, {d.year}"


def sec_filing_url(cik: object, accession: str | None) -> str | None:
    """Build the EDGAR filing-index URL for a filing.

    https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_no_dashes}/{accession}-index.htm

    Example: cik "0000789019", accession "0000950170-24-087843" ->
    https://www.sec.gov/Archives/edgar/data/789019/000095017024087843/0000950170-24-087843-index.htm

    Returns None if either input is missing or the CIK is not an integer, so callers
    can honestly say "filing reference unavailable" instead of emitting a dead link.
    """
    if cik is None or not accession:
        return None
    try:
        cik_int = int(str(cik).strip())
    except (TypeError, ValueError):
        return None
    accession_no_dashes = accession.replace("-", "")
    return (
        f"https://www.sec.gov/Archives/edgar/data/{cik_int}/"
        f"{accession_no_dashes}/{accession}-index.htm"
    )
