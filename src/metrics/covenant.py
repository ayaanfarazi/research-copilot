"""
Covenant-style screen (build_plan.md §7).

Flags leverage and coverage against ILLUSTRATIVE bands -- a screening heuristic, not
the issuer's actual covenants (those live in the credit agreement, not the 10-K).
Every output is labelled as illustrative so it can never be mistaken for a real
covenant test. Bands (illustrative): total leverage <=4 green / 4-6 amber / >6 red;
interest coverage >=3 green / 2-3 amber / <2 red.
"""

from __future__ import annotations

from src.data.models import ComputedMetric, ConfidenceTier
from src.metrics._common import FigureStore

_ILLUSTRATIVE = "ILLUSTRATIVE screening band, not a real covenant (real covenants are in the credit agreement)"


def compute_covenant_screen(store: FigureStore, year: int) -> None:
    """Produce green/amber/red flags for leverage and coverage for one fiscal year."""
    # --- Leverage flag ---
    lev = store.get("total_leverage", year)
    lev_notes = [_ILLUSTRATIVE]
    if lev is None or lev.value is None:
        # EBITDA<=0 (not_meaningful) is the worst case: earnings can't service debt.
        if lev is not None and lev.status == "not_meaningful":
            flag, lev_notes = "red", lev_notes + ["EBITDA <= 0: cannot service debt from earnings"]
        else:
            flag = "unknown"
    elif lev.value <= 4:
        flag = "green"
    elif lev.value <= 6:
        flag = "amber"
    else:
        flag = "red"
    store.add(ComputedMetric(
        name="covenant_leverage", figure_id=store.id("covenant_leverage", year),
        value=lev.value if lev else None, label=flag, unit="x",
        formula="total_leverage vs illustrative 4x/6x bands",
        component_ids=[lev.figure_id] if lev else [],
        confidence=lev.confidence if lev else ConfidenceTier.NOT_FOUND,
        notes=lev_notes,
    ))

    # --- Coverage flag ---
    cov = store.get("interest_coverage", year)
    cov_notes = [_ILLUSTRATIVE]
    if cov is None or cov.value is None:
        if cov is not None and cov.status == "not_meaningful":
            flag, cov_notes = "red", cov_notes + ["EBITDA <= 0: coverage not meaningful"]
        else:
            flag = "unknown"
    elif cov.value >= 3:
        flag = "green"
    elif cov.value >= 2:
        flag = "amber"
    else:
        flag = "red"
    store.add(ComputedMetric(
        name="covenant_coverage", figure_id=store.id("covenant_coverage", year),
        value=cov.value if cov else None, label=flag, unit="x",
        formula="interest_coverage vs illustrative 2x/3x floor",
        component_ids=[cov.figure_id] if cov else [],
        confidence=cov.confidence if cov else ConfidenceTier.NOT_FOUND,
        notes=cov_notes,
    ))
