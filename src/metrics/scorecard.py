"""
Credit risk scorecard (build_plan.md §7) -- the deterministic anchor for the AI view.

Four dimensions, each assigned a tier from documented thresholds, rolled up to a
single band by a VISIBLE, documented rule. The rule here is weakest-dimension-
dominates: a credit is only as strong as its weakest pillar, which is conservative,
credit-appropriate, and trivially explainable. The point is not a sophisticated model
-- it is an auditable verdict skeleton that the §9(A) "the view" later articulates.

NO LLM is involved: the band is pure code over the computed figures (§2 number boundary).

Severity scale shared across dimensions (higher = worse):
  0 strong | 1 adequate | 2 stretched | 3 distressed
The band is the MAX severity across the dimensions that HAVE data (the binding
constraint). A dimension whose input is missing returns severity None and is
EXCLUDED from the rollup rather than counted as distress -- "we couldn't measure
coverage" must never masquerade as "coverage is critical". Negative earnings
(EBITDA <= 0), by contrast, is a real distress signal and scores 3.
"""

from __future__ import annotations

from src.data.models import ComputedMetric, ConfidenceTier
from src.metrics._common import FigureStore, weakest_tier

# Map a severity integer to the band/tier display label.
_SEVERITY_LABEL = {0: "strong", 1: "adequate", 2: "stretched", 3: "distressed"}

# A tier is (severity | None, label, source figure). severity None == "no data,
# exclude from rollup"; it is distinct from severity 3 ("measured and distressed").
Tier = tuple[int | None, str, "ComputedMetric | None"]


def _tier_leverage(store: FigureStore, year: int) -> Tier:
    """Net leverage -> severity. Net cash is strongest; negative EBITDA is worst."""
    fig = store.get("net_leverage", year)
    if fig is None:
        return None, "no data", None
    if fig.status == "net_cash":
        return 0, "net cash", fig
    if fig.status == "not_meaningful":  # EBITDA <= 0: a real distress signal
        return 3, "earnings negative", fig
    if fig.value is None:
        return None, "no data", fig
    v = fig.value
    sev = 0 if v <= 2 else 1 if v <= 4 else 2 if v <= 6 else 3
    return sev, _SEVERITY_LABEL[sev], fig


def _tier_coverage(store: FigureStore, year: int) -> Tier:
    """Interest coverage -> severity (>6 strong / 3-6 adequate / 1.5-3 thin / <1.5 critical)."""
    fig = store.get("interest_coverage", year)
    if fig is None:
        return None, "no data", None
    if fig.status == "not_meaningful":
        return 3, "earnings negative", fig
    if fig.value is None:
        return None, "no data", fig
    v = fig.value
    sev = 0 if v > 6 else 1 if v >= 3 else 2 if v >= 1.5 else 3
    return sev, _SEVERITY_LABEL[sev], fig


def _tier_trajectory(store: FigureStore, year: int) -> Tier:
    """
    Deleveraging trajectory label -> severity (trajectory alone never forces 'distressed').

    Gated by absolute leverage: a "worsening" trend off a NET-CASH base is not a credit
    concern (the number is a negative net-leverage figure whose drift is noise), so a
    net-cash company scores 0 here regardless of the raw trend.
    """
    fig = store.get("deleveraging_trajectory", year)
    nl = store.get("net_leverage", year)
    if nl is not None and nl.status == "net_cash":
        return 0, "net cash (trajectory not binding)", fig
    mapping = {"improving": 0, "flat": 1, "worsening": 2}
    if fig is None or fig.label not in mapping:
        return None, "no data", fig
    sev = mapping[fig.label]
    # A worsening trend only binds when leverage is already at least moderate.
    # Off a low-leverage base (net leverage <= 2x) it's a yellow flag, not a driver,
    # so we cap severity at "adequate" -- otherwise a lightly-levered company that
    # added a little debt would be mislabeled "stretched".
    if nl is not None and nl.value is not None and nl.value <= 2.0:
        sev = min(sev, 1)
    return sev, fig.label, fig


def _tier_liquidity(store: FigureStore, year: int) -> Tier:
    """Liquidity runway label -> severity (comfortable/adequate/tight/acute)."""
    fig = store.get("liquidity_runway", year)
    mapping = {"comfortable": 0, "adequate": 1, "tight": 2, "acute": 3}
    if fig is None or fig.label not in mapping:
        return None, "no data", fig
    sev = mapping[fig.label]
    return sev, fig.label, fig


def compute_scorecard(store: FigureStore, year: int) -> ComputedMetric:
    """
    Roll the dimension tiers up to a credit band via weakest-link, for one year.

    Stores the four per-dimension tiers (score_*) and the final band (credit_band),
    naming the binding dimension, any excluded (no-data) dimensions, and the rule
    applied so the verdict is fully auditable.
    """
    dims = {
        "leverage": _tier_leverage(store, year),
        "coverage": _tier_coverage(store, year),
        "trajectory": _tier_trajectory(store, year),
        "liquidity": _tier_liquidity(store, year),
    }

    # Persist each dimension tier as its own figure for the UI / reasoning layer.
    comps: list[ComputedMetric] = []
    for dim, (sev, label, fig) in dims.items():
        if fig is not None:
            comps.append(fig)
        store.add(ComputedMetric(
            name=f"score_{dim}", figure_id=store.id(f"score_{dim}", year),
            value=(float(sev) if sev is not None else None),
            status=("ok" if sev is not None else "not_found"),
            label=label, unit="severity(0-3)",
            formula=f"{dim} dimension tier",
            component_ids=[fig.figure_id] if fig is not None else [],
            confidence=fig.confidence if fig is not None else ConfidenceTier.NOT_FOUND,
        ))

    # Weakest-link rollup over the dimensions that actually have data.
    measured = {dim: sev for dim, (sev, _, _) in dims.items() if sev is not None}
    excluded = [dim for dim, (sev, _, _) in dims.items() if sev is None]

    if not measured:
        # Nothing measurable -> say so honestly, don't invent a band.
        return store.add(ComputedMetric(
            name="credit_band", figure_id=store.id("credit_band", year),
            value=None, status="not_found", label="insufficient_data",
            formula="weakest-dimension-dominates (no measurable dimensions)",
            component_ids=[store.id(f"score_{d}", year) for d in dims],
            confidence=ConfidenceTier.NOT_FOUND,
            notes=["no credit dimension had data; band not assessable"],
        ))

    band_sev = max(measured.values())
    binding = [dim for dim, sev in measured.items() if sev == band_sev]
    notes = [
        f"binding dimension(s): {', '.join(binding)}",
        "rule: weakest-dimension-dominates (band = worst measured dimension)",
    ]
    if excluded:
        notes.append(f"excluded (no data): {', '.join(excluded)}")

    return store.add(ComputedMetric(
        name="credit_band", figure_id=store.id("credit_band", year),
        value=float(band_sev), label=_SEVERITY_LABEL[band_sev], unit="severity(0-3)",
        formula="weakest-dimension-dominates over measured {leverage, coverage, trajectory, liquidity}",
        component_ids=[store.id(f"score_{d}", year) for d in dims],
        confidence=weakest_tier(*comps),
        notes=notes,
    ))
