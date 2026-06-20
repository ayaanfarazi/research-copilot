"""
Survival panel -- the XBRL-only pieces (build_plan.md §7, the spine).

The footnote-extracted maturity wall (per-tranche principal/maturity) is Phase 2
(LLM extraction). Here we build the parts that come straight from the multi-year
XBRL series:
  - deleveraging trajectory  : is net leverage improving, flat, or worsening?
  - coverage durability      : is interest coverage thinning even if leverage looks flat?
  - liquidity runway         : liquidity vs near-term maturities (current debt as the
                               XBRL proxy for the maturity wall -- the graceful-degrade
                               fallback the spec calls for).

Each is categorical (a `label`) backed by the numeric change/ratio that justifies it,
with component_ids pointing at the underlying yearly figures so it stays auditable.
"""

from __future__ import annotations

from src.data.models import ComputedMetric, ConfidenceTier
from src.metrics._common import FigureStore, weakest_tier

# A net-leverage change smaller than this (in turns) is treated as "flat" -- avoids
# calling noise a trend.
_FLAT_BAND_TURNS = 0.25
_FLAT_BAND_COVERAGE = 0.25


def _series(store: FigureStore, name: str, years: list[int]) -> list[tuple[int, ComputedMetric]]:
    """Collect a metric's usable (ok/net_cash) yearly figures in ascending year order."""
    out = []
    for y in years:
        fig = store.get(name, y)
        if fig is not None and fig.value is not None and fig.status in ("ok", "net_cash"):
            out.append((y, fig))
    return out


def compute_deleveraging_trajectory(store: FigureStore, years: list[int]) -> None:
    """Direction of net leverage from the earliest to the latest usable year."""
    series = _series(store, "net_leverage", years)
    last_year = years[-1]
    if len(series) < 2:
        store.add(ComputedMetric(
            name="deleveraging_trajectory", figure_id=store.id("deleveraging_trajectory", last_year),
            value=None, status="not_found", label="insufficient_data",
            formula="net_leverage[last] - net_leverage[first]",
            confidence=ConfidenceTier.NOT_FOUND,
            notes=["need >= 2 years of net leverage"],
        ))
        return

    (_, first_fig), (_, last_fig) = series[0], series[-1]
    change = last_fig.value - first_fig.value  # negative == leverage came down == improving
    if change < -_FLAT_BAND_TURNS:
        label = "improving"
    elif change > _FLAT_BAND_TURNS:
        label = "worsening"
    else:
        label = "flat"
    store.add(ComputedMetric(
        name="deleveraging_trajectory", figure_id=store.id("deleveraging_trajectory", last_year),
        value=change, label=label, unit="x",
        formula="net_leverage[last] - net_leverage[first] (negative = deleveraging)",
        component_ids=[first_fig.figure_id, last_fig.figure_id],
        confidence=weakest_tier(first_fig, last_fig),
    ))


def compute_coverage_durability(store: FigureStore, years: list[int]) -> None:
    """Trend in interest coverage -- flags thinning coverage even when leverage is flat."""
    series = _series(store, "interest_coverage", years)
    last_year = years[-1]
    if len(series) < 2:
        store.add(ComputedMetric(
            name="coverage_durability", figure_id=store.id("coverage_durability", last_year),
            value=None, status="not_found", label="insufficient_data",
            formula="interest_coverage[last] - interest_coverage[first]",
            confidence=ConfidenceTier.NOT_FOUND,
            notes=["need >= 2 years of interest coverage"],
        ))
        return

    (_, first_fig), (_, last_fig) = series[0], series[-1]
    change = last_fig.value - first_fig.value  # positive == coverage strengthening
    if change > _FLAT_BAND_COVERAGE:
        label = "strengthening"
    elif change < -_FLAT_BAND_COVERAGE:
        label = "thinning"
    else:
        label = "stable"
    store.add(ComputedMetric(
        name="coverage_durability", figure_id=store.id("coverage_durability", last_year),
        value=change, label=label, unit="x",
        formula="interest_coverage[last] - interest_coverage[first]",
        component_ids=[first_fig.figure_id, last_fig.figure_id],
        confidence=weakest_tier(first_fig, last_fig),
    ))


def compute_liquidity_runway(store: FigureStore, year: int) -> None:
    """
    Liquidity vs near-term maturities for one year.

    Near-term maturities are proxied by current debt (the XBRL stand-in for the
    Phase-2 maturity wall). Ratio bands: >=3 comfortable, 1.5-3 adequate, 1-1.5 tight,
    <1 acute. No current debt -> comfortable (nothing due near-term).
    """
    liq = store.get("liquidity", year)
    cur_debt = store.get("debt_current", year)

    if liq is None or liq.value is None:
        store.add(ComputedMetric(
            name="liquidity_runway", figure_id=store.id("liquidity_runway", year),
            value=None, status="not_found", label="unknown",
            formula="liquidity / current_debt", confidence=ConfidenceTier.NOT_FOUND,
            notes=["liquidity unavailable"],
        ))
        return

    near_term = cur_debt.value if (cur_debt is not None and cur_debt.value is not None) else 0.0
    if near_term <= 0:
        store.add(ComputedMetric(
            name="liquidity_runway", figure_id=store.id("liquidity_runway", year),
            value=None, label="comfortable", unit="x",
            formula="liquidity / current_debt", component_ids=[liq.figure_id],
            confidence=liq.confidence,
            notes=["no current debt: no near-term maturities to cover"],
        ))
        return

    ratio = liq.value / near_term
    if ratio >= 3:
        label = "comfortable"
    elif ratio >= 1.5:
        label = "adequate"
    elif ratio >= 1.0:
        label = "tight"
    else:
        label = "acute"
    store.add(ComputedMetric(
        name="liquidity_runway", figure_id=store.id("liquidity_runway", year),
        value=ratio, label=label, unit="x",
        formula="liquidity / current_debt (near-term proxy)",
        component_ids=[liq.figure_id] + ([cur_debt.figure_id] if cur_debt else []),
        confidence=weakest_tier(liq, cur_debt),
    ))
