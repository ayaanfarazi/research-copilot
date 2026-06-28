from __future__ import annotations

from src.data.models import (
    CompanyFinancials,
    ComputedMetric,
    ConfidenceTier,
    make_figure_id,
)
from src.documents.models import FilingDocument
from src.llm.allowlist import build_enumerated_allowlist
from src.llm.client import structured_call
from src.llm.schemas.descriptive import build_figure_catalog
from src.llm.schemas.synthesis import AnchoredSynthesisPanel
from src.llm.validator import ValidationResult, ValidationViolation

_SECTION_CHAR_LIMIT = 10_000

_SPINE_CONCEPTS = [
    "credit_band",
    "score_leverage",
    "score_coverage",
    "score_trajectory",
    "score_liquidity",
    "net_leverage",
    "interest_coverage",
    "adjusted_ebitda",
    "adjusted_net_leverage",
]

_SYSTEM_PROMPT = """\
You are a credit analyst producing a structured synthesis verdict on whether a company can service its capital structure, grounded in pre-computed XBRL-based figures.

STRICT RULES — violating any causes rejection:
1. Do not write any numeric values in any text field (thesis, spine_reading, swing_factor, confidence_caveats). This includes dollar amounts, percentages, ratios, multiples, and basis points. Reference every quantitative magnitude by citing its figure_id via a Citation entry.
2. Do not write any specific year, fiscal period, or calendar date (e.g., "2024", "fiscal 2023") in a text field.
3. Every Citation must have kind="figure", ref="<figure_id>", excerpt=null. No section citations.
4. swing_factor must mention at least one figure_id directly in the text (e.g., "If net_leverage:FY2024 deteriorates...") and must have a corresponding Citation in citations.
5. verdict must be exactly one of: "can_service", "cannot_service", "conditional".
6. Set status="ok".

CONFIDENCE PROPAGATION RULE (hard — violations are flagged in post-processing):
For every figure_id you cite (i.e., appears in citations as ref) whose ConfidenceTier is LOW or NOT_FOUND, or whose status is not_meaningful, anomaly, or net_cash, you MUST:
  (a) Include a string in confidence_caveats that contains the exact figure_id AND explains why it is flagged (e.g., "net_leverage:FY2024 has status net_cash — leverage is negative by construction; not a data error but a non-standard state requiring interpretation").
  (b) Not state the figure as a precise, reliable fact in thesis, spine_reading, or swing_factor — qualify it appropriately.
  (c) When the figure catalog shows a "[flagged]" note for a figure — or for any upstream figure that feeds into a cited figure (e.g., dep_amort feeds into ebitda and adjusted_ebitda) — NAME THE ROOT CAUSE in the caveat, quoting the note text qualitatively. For example: if dep_amort:FY2024 has a flagged note stating AmortizationOfIntangibleAssets is not tagged, the confidence_caveat for adjusted_ebitda:FY2024 must say so, because that untagged element is the source of the LOW confidence that propagates through EBITDA into adjusted_ebitda.

net_cash is a real, POSITIVE state indicating more cash than debt, but leverage is negative by construction in this state and needs a caveat explaining that interpretation.

PANEL CONTRACT:
- Reason OVER the provided pre-computed figures. Never re-derive or recompute any ratio, leverage multiple, or EBITDA.
- Consume: credit_band, score_leverage, score_coverage, score_trajectory, score_liquidity, net_leverage, interest_coverage, adjusted_ebitda, adjusted_net_leverage.
- Read the spine breakdown notes (provided in the user message) rather than relying only on the band label.

OUTPUT FIELDS:
- verdict: can_service | cannot_service | conditional
- thesis: 2-4 sentences. Can the capital structure be serviced? Why?
- spine_reading: how leverage, coverage, trajectory, and band combine. Cite figures by figure_id.
- swing_factor: the one material factor that could flip this verdict. Mention a figure_id.
- confidence_caveats: one item per cited figure with LOW/NOT_FOUND/not_meaningful/anomaly/net_cash status.
- citations: all figure_id references used; kind="figure", excerpt=null.\
"""


def generate_anchored_synthesis(
    financials: CompanyFinancials,
    document: FilingDocument,
    year: int,
) -> tuple[AnchoredSynthesisPanel, ValidationResult]:
    allowlist = build_enumerated_allowlist(financials)
    catalog = build_figure_catalog(financials)
    spine_context, flagged_ids = _build_spine_context(financials, year)

    section_text = _truncate(
        document.sections.get("item_7", document.sections.get("item_1", "")),
        _SECTION_CHAR_LIMIT,
    )

    flagged_block = (
        "\n".join(f"  {fid}" for fid in flagged_ids) if flagged_ids else "  (none)"
    )

    user_message = (
        f"{catalog}\n\n"
        f"{spine_context}\n\n"
        f"=== FLAGGED FIGURES REQUIRING CAVEATS ===\n"
        f"For each of these figure_ids you cite, you MUST include its figure_id in "
        f"confidence_caveats with an explanation of why it is flagged:\n"
        f"{flagged_block}\n\n"
        f"=== MD&A EXCERPT (item_7, for swing factor grounding) ===\n"
        f"{section_text}\n\n"
        f"Produce an AnchoredSynthesisPanel for {financials.ticker} FY{year}."
    )

    panel, vr = structured_call(
        AnchoredSynthesisPanel,
        _SYSTEM_PROMPT,
        user_message,
        allowlist,
        document,
        mode="strict",
    )

    _check_confidence_gap(panel, financials, vr)
    return panel, vr


def _build_spine_context(
    financials: CompanyFinancials, year: int
) -> tuple[str, list[str]]:
    """
    Build the spine context block for the user message.

    Returns (context_text, flagged_figure_ids).
    context_text lists label/status/confidence for key scorecard figures (no values).
    flagged_figure_ids are those with LOW/NOT_FOUND confidence or anomalous status.
    """
    lines: list[str] = []
    band_notes: list[str] = []
    flagged: list[str] = []

    for concept in _SPINE_CONCEPTS:
        fig = financials.get(concept, year)
        fid = make_figure_id(concept, year)
        if fig is None:
            lines.append(f"  {fid:<44}  NOT FOUND")
            continue

        conf = fig.confidence.value
        label = getattr(fig, "label", None) or ""
        status = getattr(fig, "status", "ok")

        is_flagged = fig.confidence in (
            ConfidenceTier.LOW, ConfidenceTier.NOT_FOUND
        ) or (
            isinstance(fig, ComputedMetric)
            and status in ("not_meaningful", "anomaly", "net_cash")
        )
        if is_flagged:
            flagged.append(fid)

        marker = "  <- REQUIRES CAVEAT" if is_flagged else ""
        lines.append(
            f"  {fid:<44}  label={label!r:<22}  status={status}  "
            f"confidence={conf}{marker}"
        )

        if concept == "credit_band":
            band_notes = list(getattr(fig, "notes", []))

    context_lines = "\n".join(lines)
    notes_text = "\n  ".join(band_notes) if band_notes else "(no notes)"

    context = (
        "=== CREDIT SPINE CONTEXT (labels and status only; no values) ===\n"
        f"{context_lines}\n\n"
        "=== SPINE BREAKDOWN NOTES (from credit_band.notes) ===\n"
        f"  {notes_text}"
    )
    return context, flagged


def _check_confidence_gap(
    panel: AnchoredSynthesisPanel,
    financials: CompanyFinancials,
    vr: ValidationResult,
) -> None:
    """
    Deterministic post-generation check (not LLM-judged).

    For every figure_id cited in panel.citations whose figure has LOW/NOT_FOUND
    confidence or not_meaningful/anomaly/net_cash status, the panel's
    confidence_caveats must acknowledge that figure's uncertainty.

    Coverage is concept-level, not literal-token-level: a caveat covers a
    soft figure if either (a) the exact figure_id appears as a substring, or
    (b) the concept name (the part before ":FY") appears as a substring.
    This lets a range expression like "dep_amort:FY2020 through dep_amort:FY2024"
    cover intermediate years (dep_amort:FY2021, dep_amort:FY2022, dep_amort:FY2023)
    without requiring each year-token to appear literally.
    """
    cited_ids = {c.ref for c in panel.citations if c.kind == "figure"}
    gaps: list[str] = []

    for fid in cited_ids:
        fig = financials.figures.get(fid)
        if fig is None:
            continue
        is_flagged = fig.confidence in (
            ConfidenceTier.LOW, ConfidenceTier.NOT_FOUND
        ) or (
            isinstance(fig, ComputedMetric)
            and getattr(fig, "status", "ok") in ("not_meaningful", "anomaly", "net_cash")
        )
        if not is_flagged:
            continue
        concept = fid.split(":")[0]
        covered = any(
            fid in caveat or concept in caveat
            for caveat in panel.confidence_caveats
        )
        if not covered:
            gaps.append(fid)

    if gaps:
        panel.status = "confidence_gap"
        for fid in gaps:
            vr.violations.append(
                ValidationViolation(
                    field_path="confidence_caveats",
                    raw_token=fid,
                    canonical=fid,
                    reason="confidence_gap",
                )
            )
        vr.passed = False


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text.rfind(". ", 0, limit)
    if cut == -1:
        cut = limit
    else:
        cut += 1
    return text[:cut] + " [...truncated]"
