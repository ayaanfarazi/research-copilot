"""
Credit risk scorecard (build_plan.md §7) -- spine-driven rollup with auditable modifiers.

Rollup rule (Phase 1 v2):
  - Spine = {leverage, coverage}. The band is driven by the weakest measured spine dimension.
  - Liquidity = FLAG-ONLY (zero notches). Surfaces loudly but never moves the band.
  - Trajectory = downgrade-only modifier, max +1 severity (built on real leverage numbers).
  - Modifiers can ONLY worsen the band, never improve it.

Spine completeness:
  - Both spine dims not_found -> band = not_assessable (never strong).
  - One spine dim not_found -> compute from the present one + trajectory modifier;
    label partial_spine; cap confidence LOW; band never "strong".

Every band names its spine, modifier adjustment, and liquidity flag in notes.

Severity scale (higher = worse):
  0 strong | 1 adequate | 2 stretched | 3 distressed
"""

from __future__ import annotations

from src.data.models import ComputedMetric, ConfidenceTier
from src.metrics._common import FigureStore, weakest_tier

_SEVERITY_LABEL = {0: "strong", 1: "adequate", 2: "stretched", 3: "distressed"}

# Liquidity runway labels that trigger the refinancing-capacity flag (flag-only).
_LIQ_FLAG_LABELS = frozenset({"tight", "acute"})

Tier = tuple[int | None, str, ComputedMetric | None]


def _tier_leverage(store: FigureStore, year: int) -> Tier:
    """Net leverage -> severity. Net cash is strongest; negative EBITDA is worst."""
    fig = store.get("net_leverage", year)
    if fig is None:
        return None, "no data", None
    if fig.status == "net_cash":
        return 0, "net cash", fig
    if fig.status == "not_meaningful":
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
    Deleveraging trajectory -> severity for the downgrade-only modifier.

    improving=0, flat=1, worsening=2. Only worsening (sev >= 2) can add +1 to the band.
    Net-cash issuers score 0 regardless of trend noise.
    """
    fig = store.get("deleveraging_trajectory", year)
    nl = store.get("net_leverage", year)
    if nl is not None and nl.status == "net_cash":
        return 0, "net cash (trajectory not binding)", fig
    mapping = {"improving": 0, "flat": 1, "worsening": 2}
    if fig is None or fig.label not in mapping:
        return None, "no data", fig
    sev = mapping[fig.label]
    if nl is not None and nl.value is not None and nl.value <= 2.0:
        sev = min(sev, 1)
    return sev, fig.label, fig


def _tier_liquidity(store: FigureStore, year: int) -> Tier:
    """Liquidity runway -> severity for FLAG-ONLY surfacing (never binds the band)."""
    fig = store.get("liquidity_runway", year)
    mapping = {"comfortable": 0, "adequate": 1, "tight": 2, "acute": 3}
    if fig is None or fig.label not in mapping:
        return None, "no data", fig
    sev = mapping[fig.label]
    return sev, fig.label, fig


def _spine_severity(leverage: Tier, coverage: Tier) -> tuple[int | None, bool, list[str]]:
    """
    Compute base spine severity and whether the spine is partial.

    Returns (severity | None, partial_spine, missing_spine_dims).
    """
    spine = {"leverage": leverage[0], "coverage": coverage[0]}
    present = {k: v for k, v in spine.items() if v is not None}
    missing = [k for k, v in spine.items() if v is None]

    if not present:
        return None, False, missing

    sev = max(present.values())
    partial = len(present) == 1
    if partial and sev == 0:
        sev = 1  # partial spine never produces a "strong" band
    return sev, partial, missing


def _trajectory_adjustment(trajectory: Tier) -> int:
    """Downgrade-only: worsening adds at most +1 severity; improving/flat add nothing."""
    sev = trajectory[0]
    if sev is None:
        return 0
    return 1 if sev >= 2 else 0


def _liquidity_flag(liquidity: Tier) -> str | None:
    """Return the explicit flag text when liquidity is tight/acute; else None."""
    if liquidity[0] is None:
        return None
    if liquidity[1] in _LIQ_FLAG_LABELS:
        return "low cash vs current maturities — verify refinancing capacity"
    return None


def compute_scorecard(store: FigureStore, year: int) -> ComputedMetric:
    """
    Roll spine + modifiers into a credit band for one year.

    Persists score_* per dimension and a credit_band with auditable notes naming
    spine inputs, trajectory modifier, and liquidity flag.
    """
    leverage = _tier_leverage(store, year)
    coverage = _tier_coverage(store, year)
    trajectory = _tier_trajectory(store, year)
    liquidity = _tier_liquidity(store, year)

    dims = {
        "leverage": leverage,
        "coverage": coverage,
        "trajectory": trajectory,
        "liquidity": liquidity,
    }

    comps: list[ComputedMetric] = []
    for dim, (sev, label, fig) in dims.items():
        dim_notes = ["flag-only: does not affect band"] if dim == "liquidity" else []
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
            notes=dim_notes,
        ))

    spine_sev, partial_spine, spine_missing = _spine_severity(leverage, coverage)
    traj_adj = _trajectory_adjustment(trajectory)
    liq_flag = _liquidity_flag(liquidity)

    lev_s, cov_s = leverage[0], coverage[0]
    spine_note = (
        f"spine: leverage={leverage[1]} (sev {lev_s if lev_s is not None else 'n/a'}), "
        f"coverage={coverage[1]} (sev {cov_s if cov_s is not None else 'n/a'})"
    )

    if spine_sev is None:
        notes = [
            spine_note,
            "trajectory modifier: none (spine not assessable)",
            f"liquidity flag: {liq_flag or 'none'} ({liquidity[1]})",
            "rule: spine-driven; both leverage and coverage missing -> not_assessable",
        ]
        return store.add(ComputedMetric(
            name="credit_band", figure_id=store.id("credit_band", year),
            value=None, status="not_found", label="not_assessable",
            formula="spine-driven rollup (insufficient spine data)",
            component_ids=[store.id(f"score_{d}", year) for d in dims],
            confidence=ConfidenceTier.NOT_FOUND,
            notes=notes,
        ))

    raw_spine = max(v for v in (lev_s, cov_s) if v is not None)
    final_sev = min(3, spine_sev + traj_adj)
    band_label = _SEVERITY_LABEL[final_sev]

    traj_desc = trajectory[1] if trajectory[0] is not None else "no data"
    if traj_adj:
        modifier_note = f"trajectory modifier: +{traj_adj} ({traj_desc})"
    else:
        modifier_note = f"trajectory modifier: none ({traj_desc})"

    liq_note = (
        f"liquidity flag: {liq_flag} ({liquidity[1]})"
        if liq_flag else f"liquidity flag: none ({liquidity[1]})"
    )

    notes = [
        f"{spine_note} -> spine severity {raw_spine}"
        + (" (capped to adequate: partial_spine)" if partial_spine and raw_spine == 0 else ""),
        modifier_note,
        liq_note,
        "rule: spine=weakest-link(leverage,coverage); liquidity flag-only; trajectory downgrade-only max +1",
    ]
    if partial_spine:
        notes.append(
            f"partial_spine: missing {', '.join(spine_missing)}; confidence capped LOW; band never strong"
        )

    spine_figs = [f for f in (leverage[2], coverage[2]) if f is not None]
    conf = weakest_tier(*spine_figs, *( [trajectory[2]] if traj_adj and trajectory[2] else [] ))
    if partial_spine and conf in (ConfidenceTier.VERIFIED, ConfidenceTier.HIGH):
        conf = ConfidenceTier.LOW

    return store.add(ComputedMetric(
        name="credit_band", figure_id=store.id("credit_band", year),
        value=float(final_sev), label=band_label, unit="severity(0-3)",
        formula="spine weakest-link + trajectory downgrade-only (liquidity flag-only)",
        component_ids=[store.id(f"score_{d}", year) for d in dims],
        confidence=conf,
        notes=notes,
    ))
