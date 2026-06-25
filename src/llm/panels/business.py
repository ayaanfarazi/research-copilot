from __future__ import annotations

from src.data.models import CompanyFinancials
from src.documents.models import FilingDocument
from src.llm.allowlist import build_enumerated_allowlist
from src.llm.client import structured_call
from src.llm.schemas.descriptive import BusinessSummaryPanel, build_figure_catalog
from src.llm.validator import ValidationResult

_SECTION_CHAR_LIMIT = 20_000

_SYSTEM_PROMPT = """\
You are a financial analyst writing a structured business summary from a 10-K annual report.

STRICT RULES — violating any of these causes the output to be rejected:
1. Do not write any numeric values in any text field. This includes dollar amounts, percentages, ratios, and any other number. To reference a quantitative fact, cite it by figure_id only in a citations entry.
2. Do not write any specific year, fiscal period, or calendar date (such as "2024", "fiscal 2023") directly in a text field. When a fact is date-bearing, state it qualitatively — "in the most recent fiscal year", "since the prior acquisition" — and provide a section Citation whose excerpt is the verbatim passage from the source that contains the specific date.
3. Every Claim must include at least one citation. An uncited claim is a validation failure.
4. For section citations (kind="section"), the excerpt field must be a verbatim substring copied character-for-character from the provided Item 1 text. Do not paraphrase or truncate mid-word.
5. For figure citations (kind="figure"), set excerpt to null. Never set a non-null excerpt on a figure citation.
6. Set status to "ok".\
"""


def generate_business_summary(
    financials: CompanyFinancials,
    document: FilingDocument,
    year: int,
) -> tuple[BusinessSummaryPanel, ValidationResult]:
    allowlist = build_enumerated_allowlist(financials)
    catalog = build_figure_catalog(financials)
    section_text = _truncate(document.sections.get("item_1", ""), _SECTION_CHAR_LIMIT)
    user_message = (
        f"{catalog}\n\n"
        f"SECTION TEXT (item_1):\n{section_text}\n\n"
        f"Produce one Claim for headline (a one-sentence description of the company), "
        f"and one Claim per reportable business segment for business_lines. "
        f"Section citations must use ref=\"item_1\"."
    )
    return structured_call(
        BusinessSummaryPanel,
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
        cut += 1  # include the period
    return text[:cut] + " [...truncated]"
