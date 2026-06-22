"""
Extract numeric tokens from LLM output and normalize via the shared function.
"""

from __future__ import annotations

import re

from src.llm.normalize import extract_numeric_tokens, normalize_numeric_token

_FIGURE_ID_RE = re.compile(r"\b[\w]+:FY20\d{2}\b")
_ITEM_HEADING_RE = re.compile(r"\bItem\s+(?:1A?|1B|7A?|7|8|15)\b", re.IGNORECASE)


def scan_numeric_tokens(text: str) -> list[tuple[str, str]]:
    masked = text
    for pat in (_FIGURE_ID_RE, _ITEM_HEADING_RE):
        masked = pat.sub(lambda m: " " * len(m.group(0)), masked)

    out: list[tuple[str, str]] = []
    for raw in extract_numeric_tokens(masked):
        canonical = normalize_numeric_token(raw)
        if canonical:
            out.append((raw, canonical))
    return out
