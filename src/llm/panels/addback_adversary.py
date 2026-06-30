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
from src.llm.schemas.addback_adversary import AddBackAdversaryPanel
from src.llm.schemas.descriptive import build_figure_catalog
from src.llm.validator import ValidationResult, ValidationViolation

_SECTION_CHAR_LIMIT = 10_000

_ADDBACK_CATEGORIES = ["sbc", "restructuring", "impairment"]

_CRUX_CONCEPTS = [
    "ebitda",
    "adjusted_ebitda",
    "net_leverage",
    "adjusted_net_leverage",
]

_SYSTEM_PROMPT = """\
You are a credit analyst running an ADVERSARIAL review of a company's EBITDA add-backs. A deterministic bridge has already added certain items (share-based compensation, restructuring charges, impairments) back to EBITDA to produce adjusted EBITDA and adjusted leverage. Your job is NOT to recompute anything. Your job is to argue BOTH sides of whether those add-backs are economically legitimate for a credit analyst, then land a verdict on which leverage figure to anchor on.

STRICT RULES — violating any causes rejection:
1. Do not write any numeric values in any text field (headline, accept_case, challenge_case, leverage_read, confidence_caveats). This includes dollar amounts, percentages, ratios, multiples, basis points, and counts. Reference every quantitative magnitude by citing its figure_id.
2. Do not write any specific year, fiscal period, or calendar date (e.g., "2024", "fiscal 2023") in a text field, except as it appears inside a figure_id token (e.g., "sbc:FY2024").
3. Every Citation must have kind="figure", ref="<figure_id>", excerpt=null. No section citations.
4. accept_case and challenge_case must EACH name at least one add-back figure_id inline (e.g., "sbc:FY2024"). leverage_read must name BOTH net_leverage and adjusted_net_leverage by figure_id inline. Every figure_id named in any text field must have a corresponding Citation in citations.
5. verdict must be exactly one of: "adjusted_fair", "haircut_warranted", "reject_adjustments".
6. Set status="ok".

ADVERSARIAL CONTRACT:
- Reason OVER the provided pre-computed figures. Never re-derive or recompute any ratio, EBITDA figure, or leverage multiple. You do not know and must not guess the magnitudes; argue economic character and cite the figures.
- accept_case: the defensible case FOR each included add-back — why a standard adjusted-EBITDA definition treats it as non-cash or non-recurring (e.g., SBC is non-cash; a genuine restructuring is one-time; an impairment is a non-cash write-down).
- challenge_case: the skeptical credit case AGAINST each included add-back — why a conservative analyst haircuts or rejects it (e.g., SBC is a recurring, real economic cost of compensation that dilutes owners; serial restructuring charges are operating costs in disguise; recurring impairments signal prior overpayment and ongoing value destruction).
- leverage_read: contrast base net_leverage against adjusted_net_leverage. State which a conservative credit analyst should anchor on given the quality of the add-backs, and why. Cite both figure_ids.
- verdict: adjusted_fair = add-backs are legitimate and adjusted_net_leverage is the fair anchor; haircut_warranted = some add-backs (typically SBC) should be partially clawed back and true leverage sits between the two; reject_adjustments = add-backs are aggressive, anchor on base net_leverage.
- Address each add-back category listed as INCLUDED in the add-back context. Do not invent add-backs that are not listed as included.

CONFIDENCE PROPAGATION RULE (hard — violations are flagged in post-processing):
For every figure_id you cite whose ConfidenceTier is LOW or NOT_FOUND, or whose status is not_meaningful, anomaly, or net_cash, you MUST:
  (a) Include a string in confidence_caveats that contains the exact figure_id AND explains why it is flagged.
  (b) Not state that figure as a precise, reliable fact — qualify it.
  (c) When the figure catalog shows a "[flagged]" note for a cited figure — or for any upstream figure that feeds into a cited figure (e.g., dep_amort feeds into ebitda, which feeds into adjusted_ebitda and adjusted_net_leverage) — NAME THE ROOT CAUSE in the caveat, quoting the note text qualitatively. For example: if dep_amort:FY2024 carries a flagged note that intangible amortization is untagged, the caveat for adjusted_ebitda:FY2024 and adjusted_net_leverage:FY2024 must say so, because that untagged element is the source of the LOW confidence that propagates through EBITDA into the adjusted figures.

net_cash is a real, POSITIVE state (more cash than debt); leverage is negative by construction there and needs a caveat explaining that interpretation.

OUTPUT FIELDS:
- verdict: adjusted_fair | haircut_warranted | reject_adjustments
- headline: 2-4 sentences. Net read on how much of adjusted EBITDA rests on soft add-backs and what that means for the leverage picture.
- accept_case: the case for accepting the add-backs; name add-back figure_ids.
- challenge_case: the skeptical credit case against; name add-back figure_ids.
- leverage_read: base net_leverage vs adjusted_net_leverage; which to anchor on; name both figure_ids.
- confidence_caveats: one item per cited figure with LOW/NOT_FOUND/not_meaningful/anomaly/net_cash status.
- citations: all figure_id references used; kind="figure", excerpt=null.\
"""


def generate_addback_adversary(
    financials: CompanyFinancials,
    document: FilingDocument,
    year: int,
) -> tuple[AddBackAdversaryPanel, ValidationResult]:
    allowlist = build_enumerated_allowlist(financials)
    catalog = build_figure_catalog(financials)
    context, flagged_ids = _build_addback_context(financials, year)

    section_text = _truncate(
        document.sections.get("item_7", document.sections.get("item_1", "")),
        _SECTION_CHAR_LIMIT,
    )

    flagged_block = (
        "\n".join(f"  {fid}" for fid in flagged_ids) if flagged_ids else "  (none)"
    )

    user_message = (
        f"{catalog}\n\n"
        f"{context}\n\n"
        f"=== FLAGGED FIGURES REQUIRING CAVEATS ===\n"
        f"For each of these figure_ids you cite, you MUST include its figure_id in "
        f"confidence_caveats with an explanation of why it is flagged:\n"
        f"{flagged_block}\n\n"
        f"=== MD&A EXCERPT (item_7, for recurring-vs-one-time grounding) ===\n"
        f"{section_text}\n\n"
        f"Produce an AddBackAdversaryPanel for {financials.ticker} FY{year}."
    )

    panel, vr = structured_call(
        AddBackAdversaryPanel,
        _SYSTEM_PROMPT,
        user_message,
        allowlist,
        document,
        mode="strict",
    )

    _check_confidence_gap(panel, financials, vr)
    return panel, vr


def _build_addback_context(
    financials: CompanyFinancials, year: int
) -> tuple[str, list[str]]:
    """
    Build the add-back context block for the user message.

    Returns (context_text, flagged_figure_ids).
    context_text lists label/status/confidence for add-back categories and crux
    leverage figures (no values). flagged_figure_ids are those with LOW/NOT_FOUND
    confidence or anomalous status.
    """
    flagged: list[str] = []
    cat_lines: list[str] = []
    crux_lines: list[str] = []

    for cat in _ADDBACK_CATEGORIES:
        fig = financials.get(cat, year)
        fid = make_figure_id(cat, year)
        if fig is None or fig.value is None:
            cat_lines.append(f"  {fid:<44}  NOT PRESENT (not added back)")
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
        cat_lines.append(
            f"  {fid:<44}  label={label!r:<22}  status={status}  "
            f"confidence={conf}{marker}"
        )

    for concept in _CRUX_CONCEPTS:
        fig = financials.get(concept, year)
        fid = make_figure_id(concept, year)
        if fig is None or fig.value is None:
            crux_lines.append(f"  {fid:<44}  NOT FOUND")
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
        crux_lines.append(
            f"  {fid:<44}  label={label!r:<22}  status={status}  "
            f"confidence={conf}{marker}"
        )

    context = (
        "=== ADD-BACK CATEGORIES (included if present; labels/status only, no values) ===\n"
        + "\n".join(cat_lines)
        + "\n\n"
        "=== BRIDGE OUTPUT / LEVERAGE FIGURES (labels/status only, no values) ===\n"
        + "\n".join(crux_lines)
    )
    return context, flagged


def _check_confidence_gap(
    panel: AddBackAdversaryPanel,
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
