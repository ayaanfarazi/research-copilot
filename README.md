# AI Investment Research Copilot

Enter a US ticker and get a structured, fully-cited deal-readiness brief centered on whether the company can survive and service its capital structure. All financials are computed deterministically from SEC XBRL data — the LLM only reasons over numbers it's handed, never produces them.

## Architecture

Two pillars drive the design: a **number boundary** (the LLM reasons over figures by ID but never sources them) and a **credit spine** (the core question is debt survival, not just operating performance). SEC/XBRL ingestion, metric computation, and LLM reasoning are separate modules so the same data layer can power a future M&A tool.

## How to run

```bash
pip install -r requirements.txt          # needs a .env with ANTHROPIC_API_KEY + SEC_USER_AGENT
python scripts/smoke_test.py MSFT        # Phase 0: SEC + Anthropic connectivity
python scripts/verify_demo.py            # Phase 1: hand-verification on MSFT / TGT / VZ / JPM
python scripts/build_tag_rankings.py     # one-time: empirical /frames/ tag-frequency pull
```

Programmatic entry point:

```python
from src.pipeline import build_financials
cf = build_financials("MSFT")            # -> CompanyFinancials, every figure keyed by figure_id
cf.get("net_leverage", cf.fiscal_years[-1])
```

## Phase status

| Phase | Description | Status |
|-------|-------------|--------|
| 0 | Scaffold + SEC/Anthropic connectivity | Done |
| 1 | XBRL resolver, metrics, scorecard | Done |
| 2 | Descriptive LLM panels + maturity wall | Pending |
| 2.5 | Reasoning panels A/B/C | Pending |
| 3 | Streamlit tearsheet UI | Pending |

## Phase notes (the interview script)

**Phase 0 — plumbing.** A throttled, disk-caching SEC EDGAR client (`src/sec/`) resolves a ticker to a CIK and pulls the full XBRL `companyfacts` payload, and an Anthropic client makes a structured call. `scripts/smoke_test.py` is the exit gate: it proves we can fetch SEC data and call Claude locally.

**Phase 1 — the deterministic data spine.** This is the anti-wrapper core: every financial figure is computed in code from XBRL, never produced by an LLM. A priority-ordered tag resolver (`src/data/`) turns raw facts into tiered, fully-traceable figures or an explicit "not found", solving the three hard XBRL problems — tag variation (frequency-ranked candidate lists, validated by a `/frames/` filer-count pull), deduplication and annual isolation (period identity comes from the period **end date**, since the `fy` field is filing-scoped and shared across comparatives), and fiscal-year labeling taken from each filer's **own `fy` designation** on the primary period of every 10-K (the only per-filer-correct option — Walmart and Target have year-ends days apart but label them a year differently, so no date heuristic works). EBITDA's D&A construction method (single aggregate tag vs composed depreciation + amortization) is also **locked once for the whole window**, so a trajectory is never a method-switch artifact. On top of that, `src/metrics/` constructs EBITDA (as an explicit reconciliation), scope-aware total/net debt (never summing overlapping debt tags), operating and credit ratios (with sign-aware division so negative-EBITDA reads "not meaningful" and net cash reads as strongest), the survival panel, an illustrative covenant screen, and a weakest-link credit scorecard. `scripts/verify_demo.py` hand-verifies the output against the real 10-Ks for Microsoft (net cash), Target (Jan FYE), and Verizon (levered), and confirms financial issuers like JPMorgan are detected and degraded rather than scored with a framing that doesn't fit.
