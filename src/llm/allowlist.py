"""
Build the enumerated allowlist of canonical numeric keys from CompanyFinancials.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from src.data.models import CompanyFinancials, ComputedMetric, Figure, ResolvedFact
from src.llm.normalize import normalize_numeric_token

_RATIO_UNITS = frozenset({"x"})
_PERCENT_UNITS = frozenset({"%"})
_RATIO_NAME_HINTS = (
    "leverage", "coverage", "cagr", "margin", "roe", "turnover", "ratio", "yield"
)


@dataclass
class EnumeratedAllowlist:
    keys: frozenset[str] = field(default_factory=frozenset)
    figure_ids: frozenset[str] = field(default_factory=frozenset)


def build_enumerated_allowlist(financials: CompanyFinancials) -> EnumeratedAllowlist:
    keys: set[str] = set()
    figure_ids: set[str] = set()

    for fig in financials.figures.values():
        figure_ids.add(fig.figure_id)
        if not _is_allowed_figure(fig):
            continue
        for display in _display_forms(fig):
            canonical = normalize_numeric_token(display)
            if canonical:
                keys.add(canonical)

    return EnumeratedAllowlist(keys=frozenset(keys), figure_ids=frozenset(figure_ids))


def _is_allowed_figure(fig: Figure) -> bool:
    if fig.value is None:
        return False
    if isinstance(fig, ComputedMetric):
        return fig.status in ("ok", "net_cash")
    return True


def _metric_kind(fig: Figure) -> str:
    unit = (fig.unit or "").lower()
    name = fig.figure_id.split(":")[0].lower()
    if unit in _PERCENT_UNITS or name.endswith("_margin") or name == "roe":
        return "pct"
    if unit in _RATIO_UNITS or any(h in name for h in _RATIO_NAME_HINTS):
        return "ratio"
    return "usd"


def _display_forms(fig: Figure) -> list[str]:
    v = fig.value
    assert v is not None
    kind = _metric_kind(fig)
    if kind == "ratio":
        return _ratio_display_forms(v)
    if kind == "pct":
        return _pct_display_forms(v)
    return _usd_display_forms(v)


def _ratio_display_forms(v: float) -> list[str]:
    # x-suffixed forms only. A bare ratio form that rounds to a whole number
    # (e.g. 2.96 -> '3.0') would normalize via the integer path to a usd:N key
    # and inject a small-integer dollar key into the allowlist; x-suffix forces
    # the ratio: key. Non-integer bare ratios written by the model still match
    # via this x-key, so nothing legitimate is lost.
    d = Decimal(str(v))
    forms: list[str] = []
    for places in (1, 2):
        q = d.quantize(Decimal(10) ** -places)
        forms.append(f"{format(q, 'f')}x")
    return forms


def _pct_display_forms(v: float) -> list[str]:
    d = Decimal(str(v))
    pct = d * 100 if abs(d) <= 1 else d
    forms: list[str] = []
    for places in (1, 2):
        q = pct.quantize(Decimal(10) ** -places)
        forms.append(f"{format(q, 'f')}%")
    return forms


def _usd_display_forms(v: float) -> list[str]:
    iv = int(Decimal(str(v)).to_integral_value())
    forms: list[str] = [str(iv), f"{iv:,}"]

    millions = Decimal(iv) / Decimal("1000000")
    if millions == millions.to_integral_value():
        m_int = int(millions)
        forms.extend([str(m_int), f"{m_int:,}"])
    else:
        for places in (1, 2, 3):
            q = millions.quantize(Decimal(10) ** -places)
            forms.append(format(q, "f"))

    billions = Decimal(iv) / Decimal("1000000000")
    for places in (0, 1, 2):
        q = billions.quantize(Decimal(10) ** -places)
        b = format(q, "f")
        forms.extend([f"{b}B", f"${b}B", f"{b} billion", f"${b} billion", f"{b}bn"])

    return forms
