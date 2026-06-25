from __future__ import annotations

from src.data.models import CompanyFinancials
from src.documents.models import FilingDocument
from src.llm.allowlist import build_enumerated_allowlist
from src.llm.client import structured_call
from src.llm.schemas.descriptive import RevenueDriversPanel, build_figure_catalog
from src.llm.validator import ValidationResult

_SECTION_CHAR_LIMIT = 20_000

_SYSTEM_PROMPT = """\
You are a financial analyst describing the key drivers of revenue and profitability from a company's MD&A.

STRICT RULES — violating any of these causes the output to be rejected:
1. Do not write any numeric values in any text field. This includes dollar amounts, percentages, ratios, growth rates, basis points, and any other number. Directional language is permitted and encouraged — you may write "revenue grew", "operating margin expanded", "volume declined", "cloud demand accelerated". What is forbidden is writing the magnitude of that change ("16%", "$25B", "2.9x", "300 basis points"). To express the magnitude, cite the relevant figure_id in a Citation entry.
2. Do not write any specific year, fiscal period, or calendar date (such as "2024", "fiscal 2023", "Q3") directly in a text field. When a fact is date-bearing, state it qualitatively — "in the most recent fiscal year", "following the prior-year acquisition" — and provide a section Citation whose excerpt is the verbatim passage from the source that contains the specific date.
3. To reference a quantitative magnitude, create a Citation with kind="figure", ref="<figure_id>", excerpt=null. You must not write the number itself anywhere.
4. Every Claim must include at least one citation.
5. For section citations (kind="section"), the excerpt must be verbatim from the provided Item 7 text.
6. For figure citations (kind="figure"), set excerpt to null.
7. Populate figure_refs_used with the figure_ids you actually cited, exactly as they appear in the figure catalog.
8. Set status to "ok".\
"""


def generate_revenue_drivers(
    financials: CompanyFinancials,
    document: FilingDocument,
    year: int,
) -> tuple[RevenueDriversPanel, ValidationResult]:
    allowlist = build_enumerated_allowlist(financials)
    catalog = build_figure_catalog(financials)
    section_text = _truncate(document.sections.get("item_7", ""), _SECTION_CHAR_LIMIT)
    user_message = (
        f"{catalog}\n\n"
        f"SECTION TEXT (item_7):\n{section_text}\n\n"
        f"For each major revenue driver you identify, create one Claim. "
        f"Claim text should describe the qualitative nature of the driver. "
        f"Cite figure_ids for quantitative magnitudes; do not write numbers in text. "
        f"Section citations must use ref=\"item_7\"."
    )
    return structured_call(
        RevenueDriversPanel,
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
