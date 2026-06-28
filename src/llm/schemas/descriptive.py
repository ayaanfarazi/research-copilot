from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from src.data.models import CompanyFinancials, ComputedMetric, ConfidenceTier, ResolvedFact
from src.llm.schemas.citations import Claim


class BusinessSummaryPanel(BaseModel):
    status: Literal["ok", "validation_failed"] = "ok"
    headline: Claim               # one-sentence business description; cited to item_1
    business_lines: list[Claim]   # one Claim per reportable segment; cited to item_1


class RisksPanel(BaseModel):
    status: Literal["ok", "validation_failed"] = "ok"
    company_specific_risks: list[Claim]
    boilerplate_note: str | None = None  # no numbers or dates; null if all risks are specific


class RevenueDriversPanel(BaseModel):
    status: Literal["ok", "validation_failed"] = "ok"
    drivers: list[Claim]             # qualitative; cite figure_ids for magnitudes, no inline numbers
    segment_commentary: list[Claim]  # cross-segment dynamics; same no-number rule
    figure_refs_used: list[str]      # figure_ids actually cited (masked by _FIGURE_ID_RE before scanning)


class QoECandidatesPanel(BaseModel):
    status: Literal["ok", "validation_failed"] = "ok"
    claimed_one_time_items: list[Claim]  # surfaces candidates qualitatively; never quantifies them


def build_figure_catalog(financials: CompanyFinancials) -> str:
    """
    Build a figure catalog string for LLM prompts listing figure_ids without values.

    The LLM cites figures by figure_id; values are never exposed here (numeric injection).
    For flagged figures (LOW/NOT_FOUND confidence or not_meaningful/anomaly/net_cash status)
    the figure's notes are appended so the model understands the source of uncertainty
    and can name it in confidence_caveats.  Values still never appear; only the note text.
    """
    lines = ["Available figures (cite by figure_id; do not write values):"]
    for fig in financials.figures.values():
        if fig.value is None:
            continue
        fid = fig.figure_id
        # Skip internal scorecard / credit-band figures that are not meaningful citations.
        name_part = fid.split(":")[0]
        if name_part.startswith("score_") or name_part.startswith("credit_band"):
            continue
        period = fid.split(":")[-1] if ":" in fid else ""
        unit = fig.unit or ""
        if isinstance(fig, ComputedMetric):
            label = fig.name.replace("_", " ")
        elif isinstance(fig, ResolvedFact):
            label = fig.concept.replace("_", " ")
        else:
            label = name_part.replace("_", " ")

        is_flagged = (
            fig.confidence in (ConfidenceTier.LOW, ConfidenceTier.NOT_FOUND)
            or (
                isinstance(fig, ComputedMetric)
                and getattr(fig, "status", "ok") in ("not_meaningful", "anomaly", "net_cash")
            )
        )
        line = f"  {fid:<40} | {label:<35} | {unit:<5} | {period}"
        if is_flagged and fig.notes:
            notes_text = "; ".join(fig.notes)
            line += f" | [flagged] {notes_text}"
        lines.append(line)
    return "\n".join(lines)
