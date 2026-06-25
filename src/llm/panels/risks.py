from __future__ import annotations

from src.data.models import CompanyFinancials
from src.documents.models import FilingDocument
from src.llm.allowlist import build_enumerated_allowlist
from src.llm.client import structured_call
from src.llm.schemas.descriptive import RisksPanel, build_figure_catalog
from src.llm.validator import ValidationResult

_SECTION_CHAR_LIMIT = 20_000

_SYSTEM_PROMPT = """\
You are a financial analyst extracting material, company-specific risks from a 10-K annual report.

STRICT RULES — violating any of these causes the output to be rejected:
1. Do not write any numeric values in any text field. This includes dollar amounts, percentages, ratios, and any other number.
2. Do not write any specific year, fiscal period, or calendar date (such as "2024", "fiscal 2023") directly in a text field. When a fact is date-bearing, state it qualitatively — "in recent periods", "following last year's acquisition" — and provide a section Citation whose excerpt is the verbatim passage from the source that contains the specific date.
3. Every Claim must include at least one citation. An uncited claim is a validation failure.
4. For section citations (kind="section"), the excerpt field must be a verbatim substring copied character-for-character from the provided Item 1A text. Do not paraphrase.
5. For figure citations (kind="figure"), set excerpt to null.
6. Exclude generic boilerplate risks applicable to any public company — examples: "general economic conditions", "changes in interest rates", "competition from other companies". Include only risks specific to this company's business model, industry position, or capital structure.
7. If boilerplate_note is needed (e.g., a note that some regulatory language appears generic), write it without any numbers or dates. If not needed, set it to null.
8. Set status to "ok".\
"""


def generate_risks(
    financials: CompanyFinancials,
    document: FilingDocument,
    year: int,
) -> tuple[RisksPanel, ValidationResult]:
    allowlist = build_enumerated_allowlist(financials)
    catalog = build_figure_catalog(financials)
    section_text = _truncate(document.sections.get("item_1a", ""), _SECTION_CHAR_LIMIT)
    user_message = (
        f"{catalog}\n\n"
        f"SECTION TEXT (item_1a):\n{section_text}\n\n"
        f"Aim for five to ten specific, company-specific risks. "
        f"Section citations must use ref=\"item_1a\"."
    )
    return structured_call(
        RisksPanel,
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
