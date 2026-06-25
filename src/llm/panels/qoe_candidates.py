from __future__ import annotations

from src.data.models import CompanyFinancials
from src.documents.models import FilingDocument
from src.llm.allowlist import build_enumerated_allowlist
from src.llm.client import structured_call
from src.llm.schemas.descriptive import QoECandidatesPanel, build_figure_catalog
from src.llm.validator import ValidationResult

_ITEM7_CHAR_LIMIT = 20_000
_FOOTNOTE_CHAR_LIMIT = 10_000

_SYSTEM_PROMPT = """\
You are a financial analyst identifying potential quality-of-earnings (QoE) adjustments from a company's MD&A and debt footnote.

STRICT RULES — violating any of these causes the output to be rejected:
1. Do not write any numeric values in any text field. This includes dollar amounts, percentages, ratios, and any other number. Do not quantify any adjustment in any field.
2. Do not write any specific year, fiscal period, or calendar date (such as "2024", "fiscal 2023") directly in a text field. When a fact is date-bearing, state it qualitatively — "the recent charge", "this fiscal year's settlement" — and provide a section Citation whose excerpt is the verbatim passage from the source that contains the specific date.
3. Do not compute or state an adjusted EBITDA figure. Your role is to identify and describe candidates qualitatively; quantification is produced by the deterministic XBRL layer.
4. Every Claim must include at least one citation pointing to the specific section where management characterizes the item.
5. For section citations (kind="section"), the excerpt must be verbatim language from the source text — the exact words management used to characterize the item as non-recurring or one-time.
6. For figure citations (kind="figure"), set excerpt to null.
7. Include only items management explicitly characterizes as non-recurring, one-time, or outside the ordinary course of business. Do not invent items not supported by the source text.
8. Set status to "ok".\
"""


def generate_qoe_candidates(
    financials: CompanyFinancials,
    document: FilingDocument,
    year: int,
) -> tuple[QoECandidatesPanel, ValidationResult]:
    allowlist = build_enumerated_allowlist(financials)
    catalog = build_figure_catalog(financials)
    item7_text = _truncate(document.sections.get("item_7", ""), _ITEM7_CHAR_LIMIT)
    footnote_text = _truncate(document.sections.get("debt_footnote", ""), _FOOTNOTE_CHAR_LIMIT)
    user_message = (
        f"{catalog}\n\n"
        f"SECTION TEXT (item_7):\n{item7_text}\n\n"
        f"SECTION TEXT (debt_footnote):\n{footnote_text}\n\n"
        f"Identify potential QoE add-back candidates from the text above. "
        f"Surface items management characterizes as non-recurring or one-time. "
        f"Do not quantify them. Section citations use ref=\"item_7\" or ref=\"debt_footnote\"."
    )
    return structured_call(
        QoECandidatesPanel,
        _SYSTEM_PROMPT,
        user_message,
        allowlist,
        document,
        mode="strict",
    )


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text.rfind(". ", 0, limit)
    if cut == -1:
        cut = limit
    else:
        cut += 1
    return text[:cut] + " [...truncated]"
