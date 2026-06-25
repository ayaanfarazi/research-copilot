"""
Deterministic parser for the aggregate debt-maturities schedule (build_plan.md §7).

Two known layouts handled:
  inline   (MCD) — sentence footnote:
      "2025–$0; 2026–$2,392; 2027–$3,036; 2028–$7,221; 2029–$3,394; Thereafter-$22,573"
  linelist (VZ)  — tabular year list:
      "2025  $21,709 / 2026  7,823 / ... / Thereafter  83,203"

Returns None when no aggregate year-by-year schedule is present.  MSFT only
discloses per-issuance maturity-year ranges (e.g. "2025–2055"), not an aggregate
schedule; None is the correct return, not a failure.  Never infers buckets from
year ranges.  All principal/amount values are in millions of USD.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class MaturitySchedule:
    buckets: dict[str, int]  # keys: '2025'..'NNNN' + 'thereafter'; values in $M
    raw_matched: str         # first 400 chars of the matched footnote region
    layout: str              # 'inline' | 'linelist'


@dataclass
class ReconcileResult:
    reconciled: bool
    sum_principal: int       # sum(buckets.values()), $M
    total_debt_value: int    # XBRL carrying-value total debt, $M
    gap_pct: float           # abs gap as a fraction (0.005 = 0.5%)
    gap_abs: int             # abs(sum_principal - total_debt_value), $M
    note: str                # human-readable summary line


# ---------------------------------------------------------------------------
# Shared utilities
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    """Replace newlines with spaces; collapse runs of whitespace."""
    return re.sub(r"\s+", " ", text.replace("\n", " ")).strip()


def _to_millions(s: str) -> int:
    return int(s.replace(",", "").replace("$", "").strip())


# ---------------------------------------------------------------------------
# Layout A — inline annotation  (MCD)
#
# Trigger text (after normalisation):
#   "Aggregate maturities for 2024 debt balances ... are as follows (in millions):
#    2025–$ 0 ; 2026–$ 2,392 ; 2027–$ 3,036 ; 2028–$ 7,221 ; 2029–$ 3,394 ;
#    Thereafter-$ 22,573 ."
#
# The en-dash (U+2013) in "2025–$" and the hyphen in "Thereafter-$" are both
# captured by the [–—\-] character class.
# ---------------------------------------------------------------------------

_INLINE_TRIGGER_RE = re.compile(
    r"aggregate\s+maturities[^:]{0,250}:\s*",
    re.IGNORECASE,
)

# Matches entries like: 2026–$ 2,392   or   Thereafter-$22,573
_INLINE_ENTRY_RE = re.compile(
    r"(20\d{2}|thereafter)\s*[–—\-]+\s*\$?\s*([\d,]+)",
    re.IGNORECASE,
)


def _try_inline(norm: str) -> tuple[dict[str, int], str] | None:
    m = _INLINE_TRIGGER_RE.search(norm)
    if not m:
        return None
    chunk = norm[m.start() : m.start() + 600]
    entries = _INLINE_ENTRY_RE.findall(chunk)
    if len(entries) < 4:
        return None
    buckets: dict[str, int] = {}
    for label, amount_str in entries:
        key = "thereafter" if label.lower() == "thereafter" else label
        buckets[key] = _to_millions(amount_str)
    if "thereafter" not in buckets:
        return None
    return buckets, chunk[:400]


# ---------------------------------------------------------------------------
# Layout B — line-list table  (VZ)
#
# Trigger text (after normalisation):
#   "Maturities of long-term debt (secured and unsecured) outstanding ...
#    at December 31, 2024 are as follows:
#    Years (dollars in millions) 2025 $ 21,709 2026 7,823 ... Thereafter 83,203"
# ---------------------------------------------------------------------------

_LINELIST_TRIGGER_RE = re.compile(
    r"maturities\s+of\s+long.{0,20}debt[^.]{0,250}"
    r"(?:are\s+as\s+follows|following)[^:]*:\s*",
    re.IGNORECASE,
)

# Matches: 2025 $ 21,709   or   2026 7,823   or   Thereafter 83,203
# \b guards prevent spurious hits on "2024 are" (non-digit follows the year).
_LINELIST_ENTRY_RE = re.compile(
    r"\b(20\d{2}|thereafter)\b\s+\$?\s*([\d,]+)",
    re.IGNORECASE,
)


def _try_linelist(norm: str) -> tuple[dict[str, int], str] | None:
    m = _LINELIST_TRIGGER_RE.search(norm)
    if not m:
        return None
    chunk = norm[m.start() : m.start() + 800]
    entries = _LINELIST_ENTRY_RE.findall(chunk)
    if len(entries) < 4:
        return None
    buckets: dict[str, int] = {}
    for label, amount_str in entries:
        key = "thereafter" if label.lower() == "thereafter" else label
        if key not in buckets:  # first occurrence wins; avoids double-counting
            buckets[key] = _to_millions(amount_str)
    if len(buckets) < 4 or "thereafter" not in buckets:
        return None
    return buckets, chunk[:400]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_maturity_schedule(footnote_text: str) -> MaturitySchedule | None:
    """
    Return the aggregate year-by-year maturities schedule, or None.

    None is correct (not an error) when the footnote only discloses per-issuance
    maturity-year ranges rather than an aggregate schedule (e.g. MSFT).
    Does NOT parse per-issuance tranche tables or infer buckets from year ranges.
    """
    if not footnote_text:
        return None
    norm = _normalize(footnote_text)

    result = _try_inline(norm)
    if result is not None:
        buckets, raw = result
        return MaturitySchedule(buckets=buckets, raw_matched=raw, layout="inline")

    result = _try_linelist(norm)
    if result is not None:
        buckets, raw = result
        return MaturitySchedule(buckets=buckets, raw_matched=raw, layout="linelist")

    return None


def reconcile_schedule(
    schedule: MaturitySchedule,
    total_debt_value: int,
) -> ReconcileResult:
    """
    Compare the schedule's principal sum against the XBRL carrying-value total debt.

    Expected gap ≤ 5%: the maturities schedule reports face/principal value;
    XBRL total_debt is the carrying value after unamortised discount, issuance
    costs, and fair-value hedge adjustments — a small gap is structurally correct.
    """
    sum_p = sum(schedule.buckets.values())
    gap_abs = abs(sum_p - total_debt_value)
    gap_pct = gap_abs / total_debt_value if total_debt_value else float("inf")
    return ReconcileResult(
        reconciled=gap_pct <= 0.05,
        sum_principal=sum_p,
        total_debt_value=total_debt_value,
        gap_pct=gap_pct,
        gap_abs=gap_abs,
        note=(
            f"schedule sums to ${sum_p:,}M principal vs "
            f"${total_debt_value:,}M carrying ({gap_pct:.1%} gap)"
        ),
    )
