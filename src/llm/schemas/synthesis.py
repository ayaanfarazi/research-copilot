from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from src.llm.schemas.citations import Citation


class AnchoredSynthesisPanel(BaseModel):
    """
    Deterministic-spine-anchored credit synthesis for one fiscal year.

    Verdict and reasoning are grounded entirely in pre-computed figure_ids —
    the LLM reasons OVER them, never re-derives ratios or rewrites values.

    status="confidence_gap" is set by the post-generation confidence check (not
    by the LLM).  It means at least one cited figure with LOW/NOT_FOUND confidence
    or not_meaningful/anomaly/net_cash status was not surfaced in confidence_caveats.
    """

    status: Literal["ok", "validation_failed", "confidence_gap"] = "ok"
    verdict: Literal["can_service", "cannot_service", "conditional"]
    thesis: str                      # 2–4 sentences: can the cap structure be serviced?
    spine_reading: str               # leverage + coverage + band + trajectory; cite by figure_id
    swing_factor: str                # one thing that flips the verdict; must mention a figure_id
    confidence_caveats: list[str]    # one entry per cited LOW/NOT_FOUND/not_meaningful/anomaly/net_cash figure
    citations: list[Citation]        # kind="figure", excerpt=null only
