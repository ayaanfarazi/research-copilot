"""Document layer models for the 10-K filing and its split sections."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class FilingDocument(BaseModel):
    ticker: str
    cik: str
    accession: str           # "0000789019-24-000003"
    filed_date: str          # "2024-07-30"
    period_of_report: str    # "2024-06-30"
    primary_doc: str         # "msft-20240630.htm"
    sections: dict[str, str] = Field(default_factory=dict)
    split_quality: Literal["ok", "degraded"] = "ok"
    # Char offsets into the cleaned text string (not line numbers, not HTML offsets).
    # Populated by split.py; used by verify_documents.py to prove each section body
    # starts after the TOC region rather than inside it.
    toc_end_offset: int = 0
    item_1_start_offset: int = 0
    item_1a_start_offset: int = 0
    item_7_start_offset: int = 0
