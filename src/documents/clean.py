"""
Convert 10-K HTML to cleaned plain text for section splitting.

SEAM 4: inline XBRL ix: namespace tags (ix:nonFraction, ix:nonNumeric) are
unwrapped — the tag element is removed but its displayed text child is kept
in place. ix:header and ix:resources are fully decomposed (metadata only,
no display text). Without this step, numbers and surrounding words in NVDA
and CRM filings disappear from the extracted text, breaking phrase-anchor
checks and the debt footnote search.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup, NavigableString, Tag

_BLOCK_TAGS = frozenset({
    "p", "div", "tr", "td", "th", "li",
    "br", "hr",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "table", "thead", "tbody", "tfoot",
    "section", "article", "header", "footer",
})
_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
# ix: tags to fully discard (structural XBRL metadata, never rendered).
_IX_DISCARD = frozenset({"ix:header", "ix:resources", "ix:hidden"})


def html_to_text(html: str) -> tuple[str, list[int]]:
    """
    Parse 10-K HTML into plain text suitable for section splitting.

    Returns:
        text:            Cleaned plain text with headings prefixed by '### '.
        heading_offsets: Char offsets in text where '### ' heading lines start.
                         Used by split.py for structural navigation.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Remove non-content elements first.
    for tag in soup.find_all(["script", "style", "meta", "link", "noscript"]):
        tag.decompose()

    # Handle inline XBRL tags (SEAM 4).
    # find_all returns a static list — safe to mutate the tree during iteration.
    for tag in soup.find_all(True):
        if not tag.name:
            continue
        name_lower = tag.name.lower()
        if not name_lower.startswith("ix:"):
            continue
        if name_lower in _IX_DISCARD:
            tag.decompose()
        else:
            # ix:nonFraction, ix:nonNumeric, ix:continuation, etc.
            # Unwrap: keep displayed text children, remove the tag wrapper.
            tag.unwrap()

    lines: list[str] = []
    _walk(soup, lines)

    text = "\n".join(lines)
    # Collapse 3+ consecutive blank lines to 2.
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()

    # Collect positions of heading lines (lines starting with '### ').
    heading_offsets: list[int] = []
    pos = 0
    for line in text.splitlines(keepends=True):
        if line.startswith("### "):
            heading_offsets.append(pos)
        pos += len(line)

    return text, heading_offsets


def _walk(node: Tag | NavigableString, lines: list[str]) -> None:
    """Recursively emit plain-text lines from the parse tree."""
    if isinstance(node, NavigableString):
        text = str(node)
        text = re.sub(r"[ \t\xa0]+", " ", text).strip()
        if text:
            lines.append(text)
        return

    tag_lower = (node.name or "").lower()

    # Headings: prefix with '### ' so split.py can detect structural boundaries.
    if tag_lower in _HEADING_TAGS:
        heading_text = node.get_text(separator=" ", strip=True)
        heading_text = re.sub(r"[ \t]+", " ", heading_text).strip()
        if heading_text:
            lines.append("")
            lines.append(f"### {heading_text}")
            lines.append("")
        return

    is_block = tag_lower in _BLOCK_TAGS

    if is_block and tag_lower not in ("br", "hr"):
        lines.append("")

    if tag_lower == "br":
        lines.append("")
        return

    if tag_lower == "hr":
        lines.append("")
        lines.append("")
        return

    for child in node.children:
        _walk(child, lines)

    if is_block and tag_lower not in ("br", "hr"):
        lines.append("")
