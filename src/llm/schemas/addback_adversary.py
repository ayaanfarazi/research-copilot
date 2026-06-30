from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from src.llm.schemas.citations import Citation


class AddBackAdversaryPanel(BaseModel):
    """
    Adversarial EBITDA add-back review for one fiscal year.

    The panel argues both sides of whether included add-backs (SBC, restructuring,
    impairment) are economically legitimate and lands a verdict on which leverage
    figure a credit analyst should anchor on.

    The LLM reasons OVER pre-computed figure_ids and never re-derives values.

    status="confidence_gap" is set by the post-generation confidence check (not
    by the LLM).  It means at least one cited figure with LOW/NOT_FOUND confidence
    or not_meaningful/anomaly/net_cash status was not surfaced in confidence_caveats.
    """

    status: Literal["ok", "validation_failed", "confidence_gap"] = "ok"
    verdict: Literal["adjusted_fair", "haircut_warranted", "reject_adjustments"]
    headline: str                        # 2–4 sentences: net read on add-back quality and leverage picture
    accept_case: str                     # the case FOR the add-backs; must name add-back figure_ids
    challenge_case: str                  # the skeptical credit case AGAINST; must name add-back figure_ids
    leverage_read: str                   # base vs adjusted leverage; which to anchor; name both figure_ids
    confidence_caveats: list[str]        # one entry per cited LOW/NOT_FOUND/not_meaningful/anomaly/net_cash figure
    citations: list[Citation]            # kind="figure", excerpt=null only
