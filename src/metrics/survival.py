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

from typing import TYPE_CHECKING

from src.data.models import ComputedMetric, ConfidenceTier
from src.metrics._common import FigureStore, weakest_tier

if TYPE_CHECKING:
    from src.documents.maturities import MaturitySchedule, ReconcileResult

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
    if not years:  # defensive guard (B1): never index an empty window
        return
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
    if not years:  # defensive guard (B1): never index an empty window
        return
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


def compute_liquidity_runway(
    store: FigureStore, year: int, near_term_override: float | None = None
) -> None:
    """
    Liquidity vs near-term maturities for one year.

    near_term_override: when provided (≥0), uses this USD amount instead of
    XBRL debt_current.  Pass the next-12-month bucket from a reconciled maturity
    schedule to upgrade from proxy to footnote-based near-term.  None keeps the
    proxy path (XBRL debt_current) exactly as before.

    Ratio bands: >=3 comfortable, 1.5-3 adequate, 1-1.5 tight, <1 acute.
    No near-term maturities → comfortable (nothing due near-term).
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

    # Determine near-term maturities source: schedule bucket (preferred) or proxy.
    if near_term_override is not None:
        near_term = near_term_override
        formula = "liquidity / schedule_next12m"
        source_note = "near-term from reconciled maturity-schedule bucket"
        debt_comp_ids: list[str] = []
        confidence = weakest_tier(liq)
    else:
        near_term = cur_debt.value if (cur_debt is not None and cur_debt.value is not None) else 0.0
        formula = "liquidity / current_debt (near-term proxy)"
        source_note = "near-term via debt_current proxy (no reconciled schedule)"
        debt_comp_ids = [cur_debt.figure_id] if cur_debt else []
        confidence = weakest_tier(liq, cur_debt)

    if near_term <= 0:
        store.add(ComputedMetric(
            name="liquidity_runway", figure_id=store.id("liquidity_runway", year),
            value=None, label="comfortable", unit="x",
            formula=formula, component_ids=[liq.figure_id],
            confidence=liq.confidence,
            notes=["no near-term maturities to cover", source_note],
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
        formula=formula,
        component_ids=[liq.figure_id] + debt_comp_ids,
        confidence=confidence,
        notes=[source_note],
    ))


def compute_maturity_wall(
    store: FigureStore,
    year: int,
    schedule: "MaturitySchedule | None",
    reconcile_result: "ReconcileResult | None",
) -> None:
    """
    Persist the maturity-wall figure for the anchor year.

    label='schedule': a reconciled footnote schedule was parsed; buckets and
      reconcile note are stored in notes; value = total principal sum (USD).
    label='proxy': no reconciled schedule; debt_current remains the near-term
      stand-in in compute_liquidity_runway.  This is correct, not an error.

    Does NOT alter compute_liquidity_runway — that function must be re-called
    with near_term_override by the pipeline when a reconciled schedule exists.
    """
    figure_id = store.id("maturity_wall", year)

    if schedule is None or reconcile_result is None or not reconcile_result.reconciled:
        fail_note = (
            reconcile_result.note
            if reconcile_result is not None
            else "no footnote text provided to build_financials"
        )
        store.add(ComputedMetric(
            name="maturity_wall",
            figure_id=figure_id,
            value=None,
            label="proxy",
            formula="debt_current proxy (no reconciled aggregate schedule)",
            confidence=ConfidenceTier.LOW,
            notes=["no reconciled schedule; using current-debt proxy", fail_note],
        ))
        return

    bucket_notes = [f"{k}: ${v:,}M" for k, v in schedule.buckets.items()]
    store.add(ComputedMetric(
        name="maturity_wall",
        figure_id=figure_id,
        value=float(reconcile_result.sum_principal) * 1_000_000,
        label="schedule",
        unit="USD",
        formula="sum of maturities-schedule principal buckets (footnote-parsed)",
        confidence=ConfidenceTier.HIGH,
        notes=[reconcile_result.note] + bucket_notes,
    ))
