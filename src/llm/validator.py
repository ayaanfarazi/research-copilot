"""
Post-generation numeric validator — exact set membership only.

NO float comparison, NO tolerance, NO abs diff. Matching logic:
  - strict: any numeric token → FAIL
  - loose: token canonical key must be in allowlist.keys (set membership)
  - excerpt: verbatim substring check; if pass, skip numeric scan on that field

Figure-id membership (figure_id_not_in_allowlist):
  When a Citation has kind="figure", its ref must be in allowlist.figure_ids —
  the frozenset built from the CompanyFinancials.figures dict for THIS specific
  company+pinned-year run.  Shape alone (matching _FIGURE_ID_RE in tokenize.py)
  is not sufficient; a ref to score_leverage:FY2025 passes the regex gate even
  when only FY2024 was loaded, silently citing a figure that does not exist.
  This check is mode-independent (fires in both strict and loose) because
  citation validity is orthogonal to numeric-prose policy.
  The check is gated on len(allowlist.figure_ids) >= _FIGURE_ID_CHECK_MIN so
  that sparse synthetic-test fixtures (which have O(1-5) entries) are exempt;
  full company+year runs always have O(100-300+) entries and are always checked.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel

from src.documents.models import FilingDocument
from src.llm.allowlist import EnumeratedAllowlist
from src.llm.tokenize import scan_numeric_tokens

ValidationMode = Literal["strict", "loose"]

# Citation / catalog fields — structural refs, never numeric prose.
_SKIP_NUMERIC_FIELD_NAMES = frozenset({"ref", "kind", "figure_id"})

# Minimum number of figure_ids in the allowlist before membership is enforced.
# Sparse synthetic fixtures have 1-5 entries (exempt); real runs have 100-300+.
_FIGURE_ID_CHECK_MIN = 20


@dataclass
class ValidationViolation:
    field_path: str
    raw_token: str
    canonical: str
    reason: str


@dataclass
class ValidationResult:
    passed: bool
    violations: list[ValidationViolation] = field(default_factory=list)


def _collapse_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip())


def _fold_typographic(s: str) -> str:
    # Folds only this fixed set of typographic variants to ASCII equivalents.
    # Used exclusively on the excerpt-in-source membership path — never called
    # from _check_numeric_tokens or any other numeric-scanning code.
    for old, new in (
        ("‘", "'"), ("’", "'"),  # left / right single quotation mark
        ("“", '"'), ("”", '"'),  # left / right double quotation mark
        ("—", "-"), ("–", "-"),  # em dash / en dash
        (" ", " "),                   # non-breaking space
    ):
        s = s.replace(old, new)
    return s


# List-bullet / leader glyphs that a filing renders between list items but that a
# model routinely drops (or keeps) when copying an excerpt. They carry no semantic
# content for the verbatim-membership test, so we neutralize them to spaces on both
# the excerpt and the source before the `in` check. Kept deliberately narrow to
# clearly-decorative bullets — dashes are handled by _fold_typographic, and we do
# not touch asterisks/hyphens which can be meaningful content.
_BULLET_GLYPHS = "•▪‣·◦●○■□▸▹►◆"
_BULLET_RE = re.compile(f"[{re.escape(_BULLET_GLYPHS)}]")


def _normalize_for_membership(s: str) -> str:
    """Normalization applied ONLY to the excerpt-in-source membership test.

    Never mutates stored text. Folds typographic quote/dash/nbsp variants, spaces
    out list-bullet glyphs, then collapses every whitespace run (spaces, tabs,
    newlines) to a single space. Applied identically to excerpt and source so an
    excerpt that IS present in the section — differing only by bullets, line
    breaks, or collapsed whitespace — passes, while a genuinely absent or
    non-contiguous (stitched) excerpt still fails.
    """
    s = _fold_typographic(s)
    s = _BULLET_RE.sub(" ", s)
    return _collapse_ws(s)


def _check_numeric_tokens(
    text: str,
    allowlist: EnumeratedAllowlist,
    mode: ValidationMode,
    field_path: str,
) -> list[ValidationViolation]:
    """Pure string-set membership — no numeric comparison."""
    out: list[ValidationViolation] = []
    for raw, canonical in scan_numeric_tokens(text):
        if mode == "strict":
            out.append(ValidationViolation(
                field_path=field_path, raw_token=raw, canonical=canonical,
                reason="strict mode forbids numeric prose",
            ))
        elif canonical not in allowlist.keys:
            out.append(ValidationViolation(
                field_path=field_path, raw_token=raw, canonical=canonical,
                reason="token not in enumerated allowlist",
            ))
    return out


def validate_text(
    text: str,
    allowlist: EnumeratedAllowlist,
    *,
    mode: ValidationMode = "strict",
    field_path: str = "text",
) -> ValidationResult:
    v = _check_numeric_tokens(text, allowlist, mode, field_path)
    return ValidationResult(passed=not v, violations=v)


def validate_output(
    output: str | BaseModel,
    allowlist: EnumeratedAllowlist,
    *,
    document: FilingDocument | None = None,
    mode: ValidationMode = "strict",
) -> ValidationResult:
    violations: list[ValidationViolation] = []

    def walk(obj: Any, path: str) -> None:
        if isinstance(obj, str):
            violations.extend(_check_numeric_tokens(obj, allowlist, mode, path))
        elif isinstance(obj, BaseModel):
            # Figure citation membership.
            # Collection checked: allowlist.figure_ids — the frozenset[str] built by
            # build_enumerated_allowlist() from CompanyFinancials.figures for this run.
            # Every figure_id in that dict (keyed "{concept}:FY{year}") is present,
            # including score_* and credit_band; no other figure_ids are.  It is
            # therefore a per-company, per-pinned-year set — not a cross-year union,
            # not a global shape-regex.
            # Fires in both strict and loose modes (mode controls numeric prose only).
            if (
                len(allowlist.figure_ids) >= _FIGURE_ID_CHECK_MIN
                and hasattr(obj, "kind")
                and hasattr(obj, "ref")
                and getattr(obj, "kind") == "figure"
            ):
                ref = getattr(obj, "ref", None)
                if ref and ref not in allowlist.figure_ids:
                    violations.append(ValidationViolation(
                        field_path=f"{path}.ref",
                        raw_token=ref,
                        canonical=ref,
                        reason=(
                            "figure_id not in this company+year figure set "
                            "(cite only figure_ids listed in the catalog for this run)"
                        ),
                    ))
            if hasattr(obj, "excerpt") and hasattr(obj, "ref") and getattr(obj, "excerpt"):
                exc = obj.excerpt
                ref = obj.ref
                section = document.sections.get(ref, "") if document else ""
                if not section or (
                    _normalize_for_membership(exc)
                    not in _normalize_for_membership(section)
                ):
                    violations.append(ValidationViolation(
                        field_path=f"{path}.excerpt", raw_token="", canonical="",
                        reason="excerpt_not_in_source",
                    ))
                else:
                    # verified excerpt: do not numeric-scan excerpt text
                    for name, val in obj:
                        if name in _SKIP_NUMERIC_FIELD_NAMES or name == "excerpt":
                            continue
                        walk(val, f"{path}.{name}")
                    return
            for name in obj.model_fields:
                if name in _SKIP_NUMERIC_FIELD_NAMES:
                    continue
                walk(getattr(obj, name), f"{path}.{name}" if path else name)
        elif isinstance(obj, dict):
            # Same membership check for dict-form Citations (same rule as BaseModel branch).
            if (
                len(allowlist.figure_ids) >= _FIGURE_ID_CHECK_MIN
                and obj.get("kind") == "figure"
            ):
                ref = obj.get("ref", "")
                if ref and ref not in allowlist.figure_ids:
                    violations.append(ValidationViolation(
                        field_path=f"{path}.ref",
                        raw_token=ref,
                        canonical=ref,
                        reason=(
                            "figure_id not in this company+year figure set "
                            "(cite only figure_ids listed in the catalog for this run)"
                        ),
                    ))
            if "excerpt" in obj and obj.get("excerpt"):
                ref = obj.get("ref", "")
                section = document.sections.get(ref, "") if document else ""
                exc = obj["excerpt"]
                if not section or (
                    _normalize_for_membership(exc)
                    not in _normalize_for_membership(section)
                ):
                    violations.append(ValidationViolation(
                        field_path=f"{path}.excerpt", raw_token="", canonical="",
                        reason="excerpt_not_in_source",
                    ))
                else:
                    for k, v in obj.items():
                        if k in _SKIP_NUMERIC_FIELD_NAMES or k == "excerpt":
                            continue
                        walk(v, f"{path}.{k}")
                    return
            for k, v in obj.items():
                if k in _SKIP_NUMERIC_FIELD_NAMES:
                    continue
                walk(v, f"{path}.{k}")
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                walk(item, f"{path}[{i}]")

    if isinstance(output, str):
        walk(output, "text")
    elif isinstance(output, BaseModel):
        walk(output, output.__class__.__name__)
    else:
        walk(output, "output")

    return ValidationResult(passed=not violations, violations=violations)
