from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Citation(BaseModel):
    kind: Literal["section", "figure"]
    ref: str  # section key (item_1, item_1a, item_7, debt_footnote) or figure_id
    excerpt: str | None = None  # must be null for kind="figure"; verbatim substring for kind="section"


class Claim(BaseModel):
    text: str  # strict mode: zero numeric tokens and zero year/date tokens
    citations: list[Citation] = Field(min_length=1)
