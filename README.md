# AI Investment Research Copilot

Enter a US ticker and get a structured, fully-cited deal-readiness brief centered on whether the company can survive and service its capital structure. All financials are computed deterministically from SEC XBRL data — the LLM only reasons over numbers it's handed, never produces them.

## Architecture

Two pillars drive the design: a **number boundary** (the LLM reasons over figures by ID but never sources them) and a **credit spine** (the core question is debt survival, not just operating performance). SEC/XBRL ingestion, metric computation, and LLM reasoning are separate modules so the same data layer can power a future M&A tool.

## How to run

```bash
pip install -r requirements.txt
python scripts/smoke_test.py MSFT
```

## Phase status

| Phase | Description | Status |
|-------|-------------|--------|
| 0 | Scaffold + SEC/Anthropic connectivity | In progress |
| 1 | XBRL resolver, metrics, scorecard | Pending |
| 2 | Descriptive LLM panels + maturity wall | Pending |
| 2.5 | Reasoning panels A/B/C | Pending |
| 3 | Streamlit tearsheet UI | Pending |
