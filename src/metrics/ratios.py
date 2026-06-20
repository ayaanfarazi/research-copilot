"""
Operating + credit ratios (build_plan.md §7, §8).

All ratios are ComputedMetrics carrying their formula and component IDs. The shared
discipline (§8):
  - any missing input cascades to not_found (never a fabricated number);
  - a leverage/coverage ratio on EBITDA <= 0 is "not_meaningful" (the math works but
    a "-3.2x" actively misleads) -- these distressed cases are core, not edge;
  - net cash (net_debt < 0) yields a real "net_cash" status mapped to the strongest
    leverage reading downstream, not a sign artifact.
We deliberately SKIP FCCR (needs mandatory amortization, not cleanly extractable).
"""

from __future__ import annotations

from src.data.models import ComputedMetric, ConfidenceTier
from src.metrics._common import FigureStore, safe_div, weakest_tier


def _register(
    store: FigureStore,
    name: str,
    year: int,
    value: float | None,
    status: str,
    formula: str,
    components: list,
    unit: str,
    notes: list[str] | None = None,
) -> ComputedMetric:
    """Build + store a ratio metric, blanking the value for non-usable statuses."""
    # Only "ok" and "net_cash" carry a number; not_found/not_meaningful show no figure.
    shown = value if status in ("ok", "net_cash") else None
    confidence = ConfidenceTier.NOT_FOUND if status == "not_found" else weakest_tier(*components)
    return store.add(
        ComputedMetric(
            name=name,
            figure_id=store.id(name, year),
            value=shown,
            status=status,
            unit=unit,
            formula=formula,
            component_ids=[c.figure_id for c in components if c is not None],
            confidence=confidence,
            notes=notes or [],
        )
    )


# --- Operating panel -------------------------------------------------------

def compute_operating_ratios(store: FigureStore, year: int, prev_year: int | None) -> None:
    """Margins, growth, and ROE for one fiscal year (prev_year drives YoY/avg-equity)."""
    rev = store.get("revenue", year)
    cogs = store.get("cogs", year)
    op = store.get("operating_income", year)
    ni = store.get("net_income", year)
    eq = store.get("equity", year)

    rev_v = rev.value if rev else None

    # Gross margin = (revenue - COGS) / revenue.
    gp = (rev_v - cogs.value) if (rev_v is not None and cogs and cogs.value is not None) else None
    val, status = safe_div(gp, rev_v)
    _register(store, "gross_margin", year, (val * 100 if val is not None else None), status,
              "(revenue - cogs) / revenue", [rev, cogs], "%")

    # Operating & net margins.
    val, status = safe_div(op.value if op else None, rev_v)
    _register(store, "operating_margin", year, (val * 100 if val is not None else None), status,
              "operating_income / revenue", [op, rev], "%")
    val, status = safe_div(ni.value if ni else None, rev_v)
    _register(store, "net_margin", year, (val * 100 if val is not None else None), status,
              "net_income / revenue", [ni, rev], "%")

    # Revenue YoY growth (needs the prior year).
    prev_rev = store.get("revenue", prev_year) if prev_year is not None else None
    if prev_rev is not None and prev_rev.value:
        val, status = safe_div(rev_v - prev_rev.value, prev_rev.value)
        _register(store, "revenue_yoy", year, (val * 100 if val is not None else None), status,
                  "(revenue_t - revenue_t-1) / revenue_t-1", [rev, prev_rev], "%")

    # ROE = net income / average equity (ending equity if no prior year, flagged).
    prev_eq = store.get("equity", prev_year) if prev_year is not None else None
    notes: list[str] = []
    if eq is not None and eq.value is not None:
        if prev_eq is not None and prev_eq.value is not None:
            denom = (eq.value + prev_eq.value) / 2
        else:
            denom = eq.value
            notes.append("ROE uses ending equity (no prior-year equity for an average)")
        val, status = safe_div(ni.value if ni else None, denom)
        _register(store, "roe", year, (val * 100 if val is not None else None), status,
                  "net_income / average equity", [ni, eq, prev_eq], "%", notes)


def compute_revenue_cagr(store: FigureStore, years: list[int]) -> None:
    """Revenue CAGR over the full window: (end/start)^(1/n) - 1, stored at the last year."""
    if len(years) < 2:
        return
    start_fig = store.get("revenue", years[0])
    end_fig = store.get("revenue", years[-1])
    n = years[-1] - years[0]
    if (start_fig and start_fig.value and end_fig and end_fig.value and start_fig.value > 0 and n > 0):
        cagr = (end_fig.value / start_fig.value) ** (1 / n) - 1
        _register(store, "revenue_cagr", years[-1], cagr * 100, "ok",
                  f"(revenue_FY{years[-1]} / revenue_FY{years[0]})^(1/{n}) - 1",
                  [start_fig, end_fig], "%")
    else:
        _register(store, "revenue_cagr", years[-1], None, "not_found",
                  "revenue CAGR", [start_fig, end_fig], "%")


# --- Credit panel ----------------------------------------------------------

def compute_credit_ratios(store: FigureStore, year: int) -> None:
    """Leverage, coverage, FCF, and liquidity for one fiscal year."""
    ebitda = store.get("ebitda", year)
    total_debt = store.get("total_debt", year)
    net_debt = store.get("net_debt", year)
    interest = store.get("interest_expense", year)
    ocf = store.get("operating_cash_flow", year)
    capex = store.get("capex", year)
    cash = store.get("cash", year)
    sti = store.get("short_term_investments", year)

    ebitda_v = ebitda.value if ebitda else None

    # Total leverage = total debt / EBITDA (meaningless if EBITDA <= 0).
    val, status = safe_div(total_debt.value if total_debt else None, ebitda_v, require_positive_den=True)
    note = ["EBITDA <= 0: leverage not meaningful"] if status == "not_meaningful" else []
    _register(store, "total_leverage", year, val, status, "total_debt / EBITDA",
              [total_debt, ebitda], "x", note)

    # Net leverage = net debt / EBITDA, with explicit net-cash handling.
    nd_v = net_debt.value if net_debt else None
    if ebitda_v is not None and ebitda_v <= 0:
        _register(store, "net_leverage", year, None, "not_meaningful", "net_debt / EBITDA",
                  [net_debt, ebitda], "x", ["EBITDA <= 0: leverage not meaningful"])
    elif net_debt is not None and net_debt.status == "net_cash":
        val, _ = safe_div(nd_v, ebitda_v)
        _register(store, "net_leverage", year, val, "net_cash", "net_debt / EBITDA",
                  [net_debt, ebitda], "x", ["net cash: strongest leverage reading"])
    else:
        val, status = safe_div(nd_v, ebitda_v, require_positive_den=True)
        _register(store, "net_leverage", year, val, status, "net_debt / EBITDA",
                  [net_debt, ebitda], "x")

    # Interest coverage = EBITDA / interest (meaningless if EBITDA <= 0).
    if ebitda_v is not None and ebitda_v <= 0:
        _register(store, "interest_coverage", year, None, "not_meaningful",
                  "EBITDA / interest_expense", [ebitda, interest], "x",
                  ["EBITDA <= 0: coverage not meaningful"])
    else:
        val, status = safe_div(ebitda_v, interest.value if interest else None)
        _register(store, "interest_coverage", year, val, status,
                  "EBITDA / interest_expense", [ebitda, interest], "x")

    # FCF = OCF - capex. Assert capex sign (positive = outflow); flag if reversed.
    fcf_notes: list[str] = []
    capex_v = capex.value if capex else None
    if capex_v is not None and capex_v < 0:
        fcf_notes.append("capex tagged negative; FCF sign may be wrong -- review")
    if ocf is not None and ocf.value is not None and capex_v is not None:
        fcf_val = ocf.value - capex_v
        _register(store, "fcf", year, fcf_val, "ok", "operating_cash_flow - capex",
                  [ocf, capex], "USD", fcf_notes)
    else:
        _register(store, "fcf", year, None, "not_found", "operating_cash_flow - capex",
                  [ocf, capex], "USD", fcf_notes)

    # Cash interest coverage = (EBITDA - capex) / interest.
    if ebitda_v is not None and capex_v is not None:
        num = ebitda_v - capex_v
        val, status = safe_div(num, interest.value if interest else None)
        _register(store, "cash_interest_coverage", year, val, status,
                  "(EBITDA - capex) / interest_expense", [ebitda, capex, interest], "x")
    else:
        _register(store, "cash_interest_coverage", year, None, "not_found",
                  "(EBITDA - capex) / interest_expense", [ebitda, capex, interest], "x")

    # FCF / total debt.
    fcf_fig = store.get("fcf", year)
    val, status = safe_div(fcf_fig.value if fcf_fig else None, total_debt.value if total_debt else None)
    _register(store, "fcf_to_debt", year, (val * 100 if val is not None else None), status,
              "fcf / total_debt", [fcf_fig, total_debt], "%")

    # Liquidity = cash + short-term investments (revolver added later if extracted).
    if cash is not None and cash.value is not None:
        sti_found = sti is not None and sti.value is not None
        sti_v = sti.value if sti_found else 0.0
        liq_notes = [] if sti_found else ["ST investments treated as 0"]
        # Exclude a zeroed-missing STI from confidence (don't poison the tier).
        liq_components = [cash] + ([sti] if sti_found else [])
        _register(store, "liquidity", year, cash.value + sti_v, "ok",
                  "cash + short_term_investments", liq_components, "USD", liq_notes)
    else:
        _register(store, "liquidity", year, None, "not_found",
                  "cash + short_term_investments", [cash, sti], "USD")
