"""Deterministic QoE add-back bridge metrics.

No LLM is used here. The bridge is pure arithmetic over the Phase 1 XBRL spine:
base EBITDA plus XBRL-tagged add-backs, then adjusted net leverage.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from src.data import tag_map
from src.data.models import BridgeRow, ComputedMetric, ConfidenceTier, Figure
from src.metrics._common import FigureStore, safe_div, weakest_tier

AddBackCategory = Literal["sbc", "restructuring", "impairment"]

_ADD_BACKS: tuple[tuple[AddBackCategory, str], ...] = (
    ("sbc", "Share-based compensation"),
    ("restructuring", "Restructuring charges"),
    ("impairment", "Impairments"),
)


class QoEAddBackLine(BaseModel):
    """One found XBRL-tagged add-back line included in adjusted EBITDA."""

    category: AddBackCategory
    label: str
    concept: str
    figure_id: str
    tag: str
    value: float
    confidence: ConfidenceTier
    fiscal_year: int
    period_end: date | None = None
    accession: str | None = None
    notes: list[str] = Field(default_factory=list)


class QoEMissingAddBack(BaseModel):
    """One add-back category omitted because no XBRL fact resolved."""

    category: AddBackCategory
    label: str
    concept: str
    fiscal_year: int
    candidate_tags: list[str]
    reason: str = "not_found"


class QoEBridge(BaseModel):
    """Auditable deterministic QoE bridge for one fiscal year."""

    ticker: str
    fiscal_year: int
    base_ebitda_figure_id: str | None
    net_debt_figure_id: str | None
    addbacks: list[QoEAddBackLine]
    missing_addbacks: list[QoEMissingAddBack]
    adjusted_ebitda: ComputedMetric
    adjusted_net_leverage: ComputedMetric
    notes: list[str] = Field(default_factory=list)


def compute_qoe_bridge(store: FigureStore, ticker: str, year: int) -> QoEBridge:
    """
    Register adjusted EBITDA and adjusted net leverage, then return the bridge.

    Sign convention: add-back values are taken as reported from XBRL and added
    to base EBITDA. There is no sign flip. If a resolved add-back is negative,
    it is still included and flagged on that line so the caller can inspect the
    source tag rather than letting the value silently distort the bridge.
    """
    ebitda = store.get("ebitda", year)
    net_debt = store.get("net_debt", year)

    addbacks: list[QoEAddBackLine] = []
    missing: list[QoEMissingAddBack] = []
    components: list[Figure] = []

    for category, label in _ADD_BACKS:
        fig = store.get(category, year)
        concept = tag_map.get_concept(category)
        if fig is None or fig.value is None:
            missing.append(
                QoEMissingAddBack(
                    category=category,
                    label=label,
                    concept=category,
                    fiscal_year=year,
                    candidate_tags=[cand.tag for cand in concept.candidates],
                )
            )
            continue

        notes = list(fig.notes)
        if fig.value < 0:
            notes.append("anomalous negative add-back — verify source tag")

        addbacks.append(
            QoEAddBackLine(
                category=category,
                label=label,
                concept=category,
                figure_id=fig.figure_id,
                tag=fig.tag or "",
                value=fig.value,
                confidence=fig.confidence,
                fiscal_year=year,
                period_end=fig.period_end,
                accession=fig.accession,
                notes=notes,
            )
        )
        components.append(fig)

    bridge_notes = [
        "add-back values taken as reported from XBRL and added to base EBITDA; no sign flip"
    ]
    for miss in missing:
        bridge_notes.append(f"{miss.label} not found; omitted from adjusted EBITDA")

    adjusted_ebitda = _register_adjusted_ebitda(store, year, ebitda, addbacks, components, bridge_notes)
    adjusted_net_leverage = _register_adjusted_net_leverage(
        store, year, net_debt, adjusted_ebitda
    )

    return QoEBridge(
        ticker=ticker.upper(),
        fiscal_year=year,
        base_ebitda_figure_id=ebitda.figure_id if ebitda is not None else None,
        net_debt_figure_id=net_debt.figure_id if net_debt is not None else None,
        addbacks=addbacks,
        missing_addbacks=missing,
        adjusted_ebitda=adjusted_ebitda,
        adjusted_net_leverage=adjusted_net_leverage,
        notes=bridge_notes,
    )


def build_qoe_bridge_from_figures(ticker: str, figures: dict[str, Figure], year: int) -> QoEBridge:
    """Build a QoEBridge from an existing figure spine, registering metrics in-place."""
    store = FigureStore()
    store.figures = figures
    bridge = compute_qoe_bridge(store, ticker, year)
    figures.update(store.figures)
    return bridge


def _register_adjusted_ebitda(
    store: FigureStore,
    year: int,
    ebitda: Figure | None,
    addbacks: list[QoEAddBackLine],
    addback_figs: list[Figure],
    notes: list[str],
) -> ComputedMetric:
    if ebitda is None or ebitda.value is None:
        return store.add(
            ComputedMetric(
                name="adjusted_ebitda",
                figure_id=store.id("adjusted_ebitda", year),
                value=None,
                status="not_found",
                unit="USD",
                formula="EBITDA + XBRL add-backs",
                confidence=ConfidenceTier.NOT_FOUND,
                notes=["adjusted EBITDA not computable: EBITDA missing"] + notes,
            )
        )

    value = ebitda.value + sum(line.value for line in addbacks)
    rows = [BridgeRow(label="EBITDA", figure_id=ebitda.figure_id, value=ebitda.value)]
    rows += [
        BridgeRow(label=f"+ {line.label}", figure_id=line.figure_id, value=line.value)
        for line in addbacks
    ]
    rows.append(BridgeRow(label="= Adjusted EBITDA", figure_id=None, value=value))

    return store.add(
        ComputedMetric(
            name="adjusted_ebitda",
            figure_id=store.id("adjusted_ebitda", year),
            value=value,
            unit="USD",
            formula="EBITDA + SBC + restructuring + impairments",
            component_ids=[ebitda.figure_id] + [fig.figure_id for fig in addback_figs],
            confidence=weakest_tier(ebitda, *addback_figs),
            breakdown=rows,
            notes=notes,
        )
    )


def _register_adjusted_net_leverage(
    store: FigureStore,
    year: int,
    net_debt: Figure | None,
    adjusted_ebitda: ComputedMetric,
) -> ComputedMetric:
    adj_v = adjusted_ebitda.value
    nd_v = net_debt.value if net_debt is not None else None
    notes: list[str] = []

    if adj_v is not None and adj_v <= 0:
        value, status = None, "not_meaningful"
        notes.append("adjusted EBITDA <= 0: adjusted leverage not meaningful")
    elif net_debt is not None and getattr(net_debt, "status", None) == "net_cash":
        value, _ = safe_div(nd_v, adj_v)
        status = "net_cash"
        notes.append("net cash: adjusted net leverage is negative by construction")
    else:
        value, status = safe_div(nd_v, adj_v, require_positive_den=True)

    shown = value if status in ("ok", "net_cash") else None
    confidence = (
        ConfidenceTier.NOT_FOUND
        if status == "not_found"
        else weakest_tier(net_debt, adjusted_ebitda)
    )

    return store.add(
        ComputedMetric(
            name="adjusted_net_leverage",
            figure_id=store.id("adjusted_net_leverage", year),
            value=shown,
            status=status,
            unit="x",
            formula="net_debt / adjusted_ebitda",
            component_ids=[
                fig.figure_id for fig in (net_debt, adjusted_ebitda) if fig is not None
            ],
            confidence=confidence,
            notes=notes,
        )
    )
