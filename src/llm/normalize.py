"""
Shared numeric-token normalization for the Phase 2 validator.

CRITICAL: allowlist.py (enumerator) and tokenize.py (LLM-side extractor) MUST call
normalize_numeric_token() defined here — one function, one canonicalization path.
Exact-set membership only works if both sides produce identical keys from the same code.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

_SCALE_WORDS: dict[str, Decimal] = {
    "billion": Decimal("1000000000"),
    "million": Decimal("1000000"),
    "trillion": Decimal("1000000000000"),
    "bn": Decimal("1000000000"),
    "mm": Decimal("1000000"),
    "b": Decimal("1000000000"),
    "m": Decimal("1000000"),
    "t": Decimal("1000000000000"),
}

_TOKEN_RE = re.compile(
    r"""
    (?P<token>
        \$?\s*
        \(?\s*
        -?\d{1,3}(?:,\d{3})+(?:\.\d+)?
        |
        -?\d+(?:\.\d+)?
        \)?
        \s*
        (?:billion|million|trillion|bn|mm|[bmt])?
        \s*
        (?:x|%)?
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


def normalize_numeric_token(raw: str) -> str | None:
    """
    Map a raw numeric substring to a canonical lookup key, or None if not numeric.

    Canonical keys (string-only — no float comparison anywhere downstream):
      - usd:{integer_dollars}
      - ratio:{decimal_str}
      - pct:{decimal_str}
    """
    s = raw.strip().lower()
    if not s:
        return None

    negative = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    s = s.replace("$", "").replace(",", "").strip()

    is_ratio = s.endswith("x")
    is_pct = s.endswith("%")
    if is_ratio:
        s = s[:-1].strip()
    elif is_pct:
        s = s[:-1].strip()

    scale = Decimal("1")
    for word in sorted(_SCALE_WORDS, key=len, reverse=True):
        if s.endswith(word):
            scale = _SCALE_WORDS[word]
            s = s[: -len(word)].strip()
            break
        if len(word) == 1 and s.endswith(word) and len(s) > 1:
            scale = _SCALE_WORDS[word]
            s = s[:-1].strip()
            break

    try:
        num = Decimal(s)
    except InvalidOperation:
        return None

    if negative:
        num = -num

    if is_pct:
        return f"pct:{_decimal_key(num)}"
    if is_ratio:
        return f"ratio:{_decimal_key(num)}"

    if scale != Decimal("1"):
        dollars = num * scale
        return f"usd:{int(dollars.to_integral_value())}"

    if num == num.to_integral_value() and abs(num) >= 1:
        return f"usd:{int(num)}"

    return f"ratio:{_decimal_key(num)}"


def _decimal_key(d: Decimal) -> str:
    return format(d.normalize(), "f")


def extract_numeric_tokens(text: str) -> list[str]:
    return [m.group("token").strip() for m in _TOKEN_RE.finditer(text)]
