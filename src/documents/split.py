"""
Hybrid TOC-aware section splitter for 10-K filings.

Strategy:
  Phase A — detect the TOC cluster (first 15% of doc by char offset, ≥5 distinct
             Item numbers within a 120-line window). Record toc_end_offset.
  Phase B — search for real section anchors only AFTER toc_end_offset. A valid
             heading must be ≤120 chars and be followed within 5 lines by at least
             one line ≥80 chars (substantive text). TOC remnants and page headers
             are short and followed by another short line — this check rejects them.
  Phase C — extract section bodies between consecutive anchors.
  Phase D — delegate debt footnote extraction to debt_footnote.py.

SEAM 1: all four offset fields (toc_end_offset, item_1_start_offset,
        item_1a_start_offset, item_7_start_offset) are char offsets into the
        cleaned text string. Populated before returning the FilingDocument.

SEAM 5: TOC cluster detection is restricted to the first 15% of the document
        by char offset. This is hard-coded, not a config value. MCD has dense
        Item cross-references in franchise tables mid-document; without this
        cutoff the second cluster would be misidentified as the TOC.
"""

from __future__ import annotations

import re

from src.documents.debt_footnote import find_debt_footnote
from src.documents.models import FilingDocument

# Matches any "Item N" or "Item NA" reference — used for TOC cluster detection.
_ITEM_ANY_NUM_RE = re.compile(
    r"^\s*(?:###\s+)?item\s+(\d+[a-z]?)\b",
    re.IGNORECASE,
)

# Per-section patterns. The optional (?:###\s+)? prefix handles lines that
# clean.py emitted as heading markers ("### ITEM 1A. RISK FACTORS").
_ITEM_1_RE  = re.compile(r"^(?:###\s+)?\s*item\s+1[\.\s]",  re.IGNORECASE)
_ITEM_1A_RE = re.compile(r"^(?:###\s+)?\s*item\s+1a[\.\s]", re.IGNORECASE)
_ITEM_7_RE  = re.compile(r"^(?:###\s+)?\s*item\s+7[\.\s]",  re.IGNORECASE)
_ITEM_7A_RE = re.compile(r"^(?:###\s+)?\s*item\s+7a[\.\s]", re.IGNORECASE)
_ITEM_8_RE  = re.compile(r"^(?:###\s+)?\s*item\s+8[\.\s]",  re.IGNORECASE)

# Constrained fallback for filers that publish a glossy annual-report body with
# descriptive headings and put the SEC Item cross-reference index at the end.
_BUSINESS_SUMMARY_RE = re.compile(r"^(?:###\s+)?\s*business\s+summary\s*$", re.IGNORECASE)
_RISK_FACTORS_RE = re.compile(r"^(?:###\s+)?\s*risk\s+factors\s*$", re.IGNORECASE)
_MDA_RE = re.compile(
    r"^(?:###\s+)?\s*management'?s\s+discussion\s+and\s+analysis",
    re.IGNORECASE,
)
_FINANCIAL_STATEMENTS_RE = re.compile(
    r"^(?:###\s+)?\s*financial\s+statements\s+and\s+supplementary\s+data\s*$",
    re.IGNORECASE,
)


def split_10k(text: str, filing_doc: FilingDocument) -> FilingDocument:
    """
    Populate filing_doc.sections and all four offset fields from cleaned text.

    Modifies filing_doc in place and returns it.

    SEAM 1: offset fields are char positions in `text`, not line numbers.
    """
    lines = text.splitlines()

    # Build (line_start_char_offset, line_text) parallel list.
    line_offsets: list[int] = []
    pos = 0
    for line in lines:
        line_offsets.append(pos)
        pos += len(line) + 1  # +1 for the newline stripped by splitlines()

    # Phase A: detect TOC end offset.
    toc_end_offset = _detect_toc_end(lines, line_offsets, len(text))
    filing_doc.toc_end_offset = toc_end_offset

    # Phase B: find real section anchors post-TOC.
    item_1_off  = _find_section(lines, line_offsets, _ITEM_1_RE,  toc_end_offset)
    item_1a_off = _find_section(lines, line_offsets, _ITEM_1A_RE, toc_end_offset)
    item_7_off  = _find_section(lines, line_offsets, _ITEM_7_RE,  toc_end_offset)
    item_7a_off = _find_section(lines, line_offsets, _ITEM_7A_RE, toc_end_offset)
    item_8_off  = _find_section(lines, line_offsets, _ITEM_8_RE,  toc_end_offset)

    # Some annual-report style 10-Ks (MCD) use descriptive body headings and
    # relegate "Item N" labels to an end-of-document cross-reference index.
    # MCD-style filers: toc_end_offset=0 (no Item N TOC detected);
    # toc_trap assertion passes via 5000-char floor only.
    if item_1_off is None:
        item_1_off = _find_descriptive_section(lines, line_offsets, _BUSINESS_SUMMARY_RE, toc_end_offset)
    if item_1a_off is None:
        item_1a_off = _find_descriptive_section(lines, line_offsets, _RISK_FACTORS_RE, toc_end_offset)
    if item_7_off is None:
        item_7_off = _find_descriptive_section(lines, line_offsets, _MDA_RE, toc_end_offset)
    if item_8_off is None:
        item_8_off = _find_descriptive_section(lines, line_offsets, _FINANCIAL_STATEMENTS_RE, toc_end_offset)

    # SEAM 1: populate offset fields before returning.
    filing_doc.item_1_start_offset  = item_1_off  or 0
    filing_doc.item_1a_start_offset = item_1a_off or 0
    filing_doc.item_7_start_offset  = item_7_off  or 0

    # Phase C: extract section bodies.
    if item_1_off is not None:
        end = _next_boundary_after(item_1_off, item_1a_off, item_7_off, item_8_off, doc_len=len(text))
        filing_doc.sections["item_1"] = text[item_1_off:end].strip()

    if item_1a_off is not None:
        end = _next_boundary_after(item_1a_off, item_7_off, item_8_off, doc_len=len(text))
        filing_doc.sections["item_1a"] = text[item_1a_off:end].strip()

    if item_7_off is not None:
        end = _next_boundary_after(item_7_off, item_7a_off, item_1a_off, item_8_off, doc_len=len(text))
        filing_doc.sections["item_7"] = text[item_7_off:end].strip()

    # Mark degraded if any core section is absent.
    missing = [s for s in ("item_1", "item_1a", "item_7") if not filing_doc.sections.get(s)]
    if missing:
        filing_doc.split_quality = "degraded"

    # Phase D: debt footnote from Item 8 onward (fall back to Item 7 region if 8 absent).
    footnote_search_start = item_8_off or item_7_off or 0
    footnote = find_debt_footnote(text[footnote_search_start:])
    filing_doc.sections["debt_footnote"] = footnote
    if not footnote:
        filing_doc.split_quality = "degraded"

    return filing_doc


def _detect_toc_end(lines: list[str], line_offsets: list[int], doc_len: int) -> int:
    """
    Detect the char offset marking the end of the Table of Contents region.

    SEAM 5: candidate lines must begin within the first 15% of the document
    by char offset — hard-coded, not configurable.

    Finds the window of ≤120 lines (within the 15% cutoff) that contains ≥5
    distinct Item numbers. Returns the char offset of the last character on
    the final line of that window, or 0 if no TOC is detected.
    """
    cutoff_offset = int(doc_len * 0.15)

    # Pre-filter to lines within the cutoff.
    eligible: list[tuple[int, int, str]] = [  # (line_idx, char_offset, line_text)
        (i, off, line)
        for i, (off, line) in enumerate(zip(line_offsets, lines))
        if off <= cutoff_offset
    ]

    best_end_offset = 0

    for start in range(len(eligible)):
        window = eligible[start : start + 120]
        distinct_items: set[str] = set()
        last_line_end = 0
        for line_idx, char_off, line in window:
            m = _ITEM_ANY_NUM_RE.match(line)
            if m:
                if _has_substantive_followthrough(lines, line_idx):
                    continue
                distinct_items.add(m.group(1).lower())
                last_line_end = char_off + len(line)
        if len(distinct_items) >= 5:
            best_end_offset = max(best_end_offset, last_line_end)

    return best_end_offset


def _find_section(
    lines: list[str],
    line_offsets: list[int],
    pattern: re.Pattern[str],
    toc_end_offset: int,
) -> int | None:
    """
    Return the char offset of the first valid section heading after toc_end_offset.

    A valid heading must satisfy:
      - char offset > toc_end_offset
      - line length ≤ 120 chars (excludes long prose lines that happen to mention an Item)
      - at least one of the next 5 non-empty lines is ≥ 80 chars (substantive text follows)

    The third check rejects TOC remnants and running headers: they are short lines
    followed by another short line (a page number or the next Item reference).
    """
    for i, (off, line) in enumerate(zip(line_offsets, lines)):
        if off <= toc_end_offset:
            continue
        if not pattern.match(line):
            continue
        if len(line) > 120:
            continue
        if _has_substantive_followthrough(lines, i):
            return off
        # No substantive follow-through - skip.
    return None


def _find_descriptive_section(
    lines: list[str],
    line_offsets: list[int],
    pattern: re.Pattern[str],
    toc_end_offset: int,
) -> int | None:
    """
    Find annual-report style descriptive headings while rejecting TOC rows.

    These filings often list "Business Summary ... 3" in the TOC and then use
    the same descriptive heading in the body. A following page-number line is
    the strongest signal that the match is still the TOC.
    """
    for i, (off, line) in enumerate(zip(line_offsets, lines)):
        if off <= toc_end_offset:
            continue
        if not pattern.match(line):
            continue
        if len(line) > 120:
            continue
        if _next_nonempty_is_page_number(lines, i):
            continue
        for j in range(i + 1, min(i + 9, len(lines))):
            if len(lines[j].strip()) >= 80:
                return off
    return None


def _next_boundary_after(start: int, *candidates: int | None, doc_len: int) -> int:
    later = [candidate for candidate in candidates if candidate is not None and candidate > start]
    return min(later) if later else doc_len


def _has_substantive_followthrough(lines: list[str], line_idx: int) -> bool:
    """Return True when a heading is followed by real body text within 5 lines."""
    for j in range(line_idx + 1, min(line_idx + 6, len(lines))):
        if len(lines[j].strip()) >= 80:
            return True
    return False


def _next_nonempty_is_page_number(lines: list[str], line_idx: int) -> bool:
    for j in range(line_idx + 1, min(line_idx + 5, len(lines))):
        value = lines[j].strip()
        if value:
            return bool(re.fullmatch(r"(?:page\s*)?\d{1,3}", value, re.IGNORECASE))
    return False
