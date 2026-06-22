"""
Locate the debt/long-term borrowings note within the Notes to Financial Statements.

SEAM 6 (VZ multi-note tie-break): if multiple candidate notes match the debt-note
regex, take the one with the largest len(extracted_body), where extracted_body is
the text from the matched note heading to the next note heading. "First match" and
"latest match" are both wrong for VZ, which has multiple notes with debt-adjacent
headings; the primary debt note is by far the longest.
"""

from __future__ import annotations

import re

# Pattern that identifies any note heading (e.g. "NOTE 5." or "5. Long-Term Debt").
_NOTE_HEADING_RE = re.compile(
    r"^(?:note\s+\d+[\.\s—–-]|\d+\.\s)",
    re.IGNORECASE | re.MULTILINE,
)

# Pattern that identifies a note as the debt/borrowings note based on its heading.
_DEBT_HEADING_RE = re.compile(
    r"(?:^\s*(?:note\s+\d+[\.\s—–-]|\d+\.\s)[\s\S]{0,160}\bdebt\b"
    r"|long.{0,6}term\s+debt"
    r"|long.{0,6}term\s+borrow"
    r"|notes?\s+payable"
    r"|credit\s+facilit"
    r"|debt\s+and\s+(?:credit|financing|borrow)"
    r"|long.{0,6}term\s+financing)",
    re.IGNORECASE,
)

_UNNUMBERED_DEBT_HEADING_RE = re.compile(
    r"^\s*debt\s+financing\s*$",
    re.IGNORECASE | re.MULTILINE,
)

_UNNUMBERED_NOTE_END_RE = re.compile(
    r"^\s*(?:share-based\s+compensation|financial\s+instruments|fair\s+value"
    r"|income\s+taxes|segment\s+and\s+geographic\s+information|leases)\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def find_debt_footnote(text: str) -> str:
    """
    Return the body of the debt footnote, or '' if not found.

    Searches the supplied text (typically from Item 8 onward) for notes
    whose heading area matches the debt-note regex.

    SEAM 6: tie-break is largest len(extracted_body) — see module docstring.
    """
    note_starts = [m.start() for m in _NOTE_HEADING_RE.finditer(text)]

    if not note_starts:
        return _find_unnumbered_debt_note(text)

    candidates: list[str] = []
    for idx, start in enumerate(note_starts):
        end = note_starts[idx + 1] if idx + 1 < len(note_starts) else len(text)
        body = text[start:end]
        # Inspect only the first 300 chars for the heading/title match —
        # avoids false positives from body text that mentions "long-term debt".
        if _DEBT_HEADING_RE.search(body[:300]):
            candidates.append(body)

    if not candidates:
        return ""

    # SEAM 6: explicit tie-break — largest extracted_body wins.
    return max(candidates, key=len)


def _find_unnumbered_debt_note(text: str) -> str:
    """Fallback for annual-report notes headed by title only, e.g. MCD."""
    candidates: list[str] = []
    for match in _UNNUMBERED_DEBT_HEADING_RE.finditer(text):
        start = match.start()
        next_heading = _UNNUMBERED_NOTE_END_RE.search(text, match.end())
        end = next_heading.start() if next_heading else min(len(text), start + 15000)
        candidates.append(text[start:end])

    if not candidates:
        return ""

    # Preserve the VZ tie-break principle for fallback candidates too.
    return max(candidates, key=len)
