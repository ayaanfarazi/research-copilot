"""
Empirical tag-frequency pull (build_plan.md §6 component 1).

For each candidate tag in the concept catalogue, ask the SEC `/frames/` endpoint how
many filers reported that tag for a given fiscal period, and write the counts to
data/tag_rankings.json. tag_map.py loads that file to back its confidence tiers (and,
for non-scope-sensitive concepts, its ordering) with real frequencies rather than a
hand guess.

The period format MATTERS and differs by concept type (verified against the live API):
  - flow / duration concepts:  /frames/us-gaap/{Tag}/{unit}/CY{year}.json
  - instant / balance-sheet:    /frames/us-gaap/{Tag}/{unit}/CY{year}Q4I.json
A flow tag queried with the instant frame (or vice versa) returns ~nothing, which would
silently zero out exactly the credit-critical stock concepts -- so we branch on is_flow.

Run:  python scripts/build_tag_rankings.py [year]   (default year below)
This is a one-time-ish pull; responses are cached on disk by the SEC client.
"""

import json
import sys

sys.path.insert(0, ".")

import requests  # noqa: E402

import config  # noqa: E402
from src.data import tag_map  # noqa: E402
from src.sec.client import get_json  # noqa: E402

# A recent, fully-filed fiscal period. Q4 instant frames use the calendar-year-end snapshot.
_DEFAULT_YEAR = 2023

# The /frames/ endpoint spells units differently than companyfacts' unit keys.
_UNIT_FOR_FRAMES = {"USD": "USD", "shares": "shares", "USD/shares": "USD-per-shares"}


def _frame_url(tag: str, unit: str, year: int, is_flow: bool) -> str:
    frame = f"CY{year}" if is_flow else f"CY{year}Q4I"
    return f"{config.SEC_BASE_URL}/api/xbrl/frames/us-gaap/{tag}/{unit}/{frame}.json"


def _filer_count(tag: str, unit: str, year: int, is_flow: bool) -> int | None:
    """Number of distinct filers reporting `tag` for the period, or None if the tag has no frame."""
    url = _frame_url(tag, unit, year, is_flow)
    try:
        data = get_json(url)
    except requests.HTTPError:
        # 404 = no frame for this tag/period combination (often the wrong unit/period
        # form, or a tag no filer uses). Treat as "not present" rather than crashing.
        return None
    return len(data.get("data", []))


def run(year: int) -> None:
    rankings: dict[str, dict[str, int | None]] = {}
    for name, concept in tag_map.CONCEPTS.items():
        unit = _UNIT_FOR_FRAMES.get(concept.unit, concept.unit)
        counts: dict[str, int | None] = {}
        for cand in concept.candidates:
            counts[cand.tag] = _filer_count(cand.tag, unit, year, concept.is_flow)
        rankings[name] = counts
        # Print a readable ranking (descending by count) for the build log / README notes.
        ordered = sorted(counts.items(), key=lambda kv: (kv[1] is None, -(kv[1] or 0)))
        print(f"{name} ({'flow' if concept.is_flow else 'instant'}):")
        for tag, cnt in ordered:
            print(f"    {cnt if cnt is not None else 'none':>8}  {tag}")

    out_path = config.CACHE_DIR.parent / "tag_rankings.json"
    out_path.write_text(json.dumps({"year": year, "rankings": rankings}, indent=2))
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    yr = int(sys.argv[1]) if len(sys.argv) > 1 else _DEFAULT_YEAR
    run(yr)
