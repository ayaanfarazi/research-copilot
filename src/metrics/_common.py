"""
Shared helpers for the deterministic metrics layer (build_plan.md §7).

Three things every metric module needs:
  - FigureStore : a registry that holds every ResolvedFact/ComputedMetric by ID and
    lets a metric look up the numeric value of any input by (name, year).
  - weakest_tier: a metric is only as trustworthy as its least-trustworthy input, so
    derived figures inherit the weakest confidence tier of their components.
  - safe_div    : division that refuses to produce a misleading number — it guards
    None inputs (not_found cascade, §6 component 4) AND denominator SIGN, because a
    leverage/coverage ratio on zero-or-negative EBITDA is mathematically defined but
    economically meaningless (§8).

NO LLM is used anywhere in this layer (the §2 number boundary): every value here is
pure arithmetic over resolved XBRL facts.
"""

from __future__ import annotations

from src.data.models import ComputedMetric, ConfidenceTier, Figure, ResolvedFact, make_figure_id

# Strength order, strongest first. Used to pick the weakest tier among inputs.
_TIER_ORDER = [
    ConfidenceTier.VERIFIED,
    ConfidenceTier.HIGH,
    ConfidenceTier.LOW,
    ConfidenceTier.NOT_FOUND,
]


def weakest_tier(*figs: Figure | None) -> ConfidenceTier:
    """Return the least-trustworthy tier among the inputs (missing input -> NOT_FOUND)."""
    worst = ConfidenceTier.VERIFIED
    for f in figs:
        tier = ConfidenceTier.NOT_FOUND if f is None else f.confidence
        if _TIER_ORDER.index(tier) > _TIER_ORDER.index(worst):
            worst = tier
    return worst


def safe_div(
    num: float | None, den: float | None, *, require_positive_den: bool = False
) -> tuple[float | None, str]:
    """
    Divide num/den, returning (value, status).

    status is one of the FigureStatus strings:
      - "not_found"      : an input is missing, or denominator is exactly 0.
      - "not_meaningful" : require_positive_den is set and denominator <= 0
                           (e.g. leverage when EBITDA is negative).
      - "ok"             : a usable number.
    """
    if num is None or den is None:
        return None, "not_found"
    if den == 0:
        return None, "not_found"
    if require_positive_den and den <= 0:
        return None, "not_meaningful"
    return num / den, "ok"


class FigureStore:
    """
    Registry of every figure produced for a company, keyed by figure_id.

    Metric functions read inputs via `value()`/`get()` and register their output via
    `add()`. Because both raw facts and computed metrics share the `name:FY{year}`
    ID scheme, a metric can pull either kind of input with the same call.
    """

    def __init__(self) -> None:
        self.figures: dict[str, Figure] = {}

    def add(self, fig: Figure) -> Figure:
        self.figures[fig.figure_id] = fig
        return fig

    def get(self, name: str, year: int) -> Figure | None:
        return self.figures.get(make_figure_id(name, year))

    def value(self, name: str, year: int) -> float | None:
        """Numeric value of a figure, or None if absent/not-found."""
        fig = self.get(name, year)
        return fig.value if fig is not None else None

    def id(self, name: str, year: int) -> str:
        return make_figure_id(name, year)
