"""
Constructed metrics (build_plan.md §7): EBITDA, total debt, net debt.

These are the "constructed inputs (with judgment calls)" the spec calls out. Each is
rendered as a ComputedMetric that records its formula and the figure_ids it was built
from, and EBITDA additionally carries an explicit reconciliation (`breakdown`) so the
number is auditable rather than opaque. Every judgment call (lease treatment, D&A
composition, treating a missing current-debt tag as zero) is written into `notes`.
"""

from __future__ import annotations

from src.data.models import BridgeRow, ComputedMetric, ConfidenceTier
from src.metrics._common import FigureStore, weakest_tier


_DA_COMPONENTS = ("depreciation", "amortization_intangibles")


def da_plan(store: FigureStore, years: list[int]) -> dict:
    """
    Decide the D&A construction method ONCE for the whole window (series consistency).

    Method is chosen from the latest year (the resolver's anchor year):
      - "aggregate" if a single D&A aggregate tag resolved there;
      - else "composed" (depreciation + intangible amortization).
    For "composed", we also record the component set present in the latest year as the
    reference, so any year whose components differ can be flagged -- the same way tag
    switches are flagged -- rather than silently changing the EBITDA build mid-series.
    """
    latest = years[-1]
    agg = store.get("dep_amort", latest)
    if agg is not None and agg.value is not None:
        return {"method": "aggregate", "ref_components": None}
    ref = {c for c in _DA_COMPONENTS
           if (f := store.get(c, latest)) is not None and f.value is not None}
    return {"method": "composed", "ref_components": ref}


def _compose_da(
    store: FigureStore, year: int, plan: dict
) -> tuple[float | None, list[BridgeRow], list, list[str]]:
    """
    Build total D&A for one year using the window-locked method (`plan`).

    Returns (value, breakdown_rows, component_figs, notes). A year that cannot follow
    the locked method is flagged (series inconsistency) rather than silently switching.
    """
    if plan["method"] == "aggregate":
        agg = store.get("dep_amort", year)
        if agg is not None and agg.value is not None:
            return agg.value, [BridgeRow(label="+ D&A", figure_id=agg.figure_id, value=agg.value)], [agg], []
        return None, [], [], [
            f"D&A method=aggregate (locked for series) but no aggregate tag for FY{year}: inconsistency"
        ]

    # Composed method: sum the available components, each as a tagged bridge row.
    parts = [store.get(c, year) for c in _DA_COMPONENTS]
    present = [p for p in parts if p is not None and p.value is not None]
    if not present:
        return None, [], [], ["D&A not found (composed method, no components present)"]

    total = sum(p.value for p in present)
    rows = [BridgeRow(label=f"+ {p.concept}", figure_id=p.figure_id, value=p.value) for p in present]
    notes = ["D&A composed from: " + " + ".join(p.concept for p in present)]

    # Flag if this year's component set differs from the latest year's -- a silent
    # change in what goes into D&A would distort the EBITDA trajectory.
    used = {p.concept for p in present}
    ref = plan.get("ref_components") or set()
    if ref and used != ref:
        notes.append(
            f"D&A components differ from latest-year set {sorted(ref)} (series inconsistency)"
        )
    return total, rows, present, notes


def compute_ebitda(store: FigureStore, year: int, plan: dict) -> ComputedMetric:
    """
    EBITDA = operating income + D&A (primary), or NI + interest + taxes + D&A (fallback).

    `plan` is the window-locked D&A method (see da_plan) so the construction is the same
    across all years. Rendered as a reconciliation. If the inputs for neither build are
    present, the result is an explicit not_found rather than a partial/misleading number.
    """
    op = store.get("operating_income", year)
    da_value, da_rows, da_figs, da_notes = _compose_da(store, year, plan)

    # Primary build: operating income + D&A.
    if op is not None and op.value is not None and da_value is not None:
        value = op.value + da_value
        rows = [BridgeRow(label="Operating income", figure_id=op.figure_id, value=op.value)]
        rows += da_rows
        rows.append(BridgeRow(label="= EBITDA", figure_id=None, value=value))
        return store.add(
            ComputedMetric(
                name="ebitda",
                figure_id=store.id("ebitda", year),
                value=value,
                unit="USD",
                formula="operating_income + D&A",
                component_ids=[op.figure_id] + [f.figure_id for f in da_figs],
                confidence=weakest_tier(op, *da_figs),
                breakdown=rows,
                notes=da_notes,
            )
        )

    # Fallback build: net income + interest + taxes + D&A.
    ni = store.get("net_income", year)
    interest = store.get("interest_expense", year)
    tax = store.get("tax_expense", year)
    fallback_inputs = [ni, interest, tax]
    if all(f is not None and f.value is not None for f in fallback_inputs) and da_value is not None:
        value = ni.value + interest.value + tax.value + da_value
        rows = [
            BridgeRow(label="Net income", figure_id=ni.figure_id, value=ni.value),
            BridgeRow(label="+ Interest", figure_id=interest.figure_id, value=interest.value),
            BridgeRow(label="+ Taxes", figure_id=tax.figure_id, value=tax.value),
            *da_rows,
            BridgeRow(label="= EBITDA", figure_id=None, value=value),
        ]
        return store.add(
            ComputedMetric(
                name="ebitda",
                figure_id=store.id("ebitda", year),
                value=value,
                unit="USD",
                formula="net_income + interest + taxes + D&A (fallback build)",
                component_ids=[f.figure_id for f in fallback_inputs] + [f.figure_id for f in da_figs],
                confidence=weakest_tier(*fallback_inputs, *da_figs),
                breakdown=rows,
                notes=["used fallback EBITDA build (operating income unavailable)"] + da_notes,
            )
        )

    # Neither build possible -> honest not_found.
    return store.add(
        ComputedMetric(
            name="ebitda",
            figure_id=store.id("ebitda", year),
            value=None,
            status="not_found",
            unit="USD",
            formula="operating_income + D&A",
            confidence=ConfidenceTier.NOT_FOUND,
            notes=["EBITDA not computable: missing operating income/D&A and fallback inputs"]
            + da_notes,
        )
    )


def compute_total_debt(store: FigureStore, year: int, with_leases: bool = False) -> ComputedMetric:
    """
    Total debt, scope-aware so overlapping tags are never summed (§7).

    Preference: a single `debt_total` tag if present; else current_only + noncurrent_only.
    A missing current-debt tag is treated as 0 (companies with no current maturities
    legitimately omit it) but the assumption is written into notes -- not silent.
    Operating leases are excluded by default; `with_leases=True` adds them.
    """
    notes: list[str] = []
    total = store.get("debt_total", year)

    if total is not None and total.value is not None:
        value = total.value
        components = [total]
        formula = "debt_total"
    else:
        cur = store.get("debt_current", year)
        non = store.get("debt_noncurrent", year)
        if non is None or non.value is None:
            # Without a noncurrent figure (and no total), we can't honestly state total debt.
            return store.add(
                ComputedMetric(
                    name="total_debt",
                    figure_id=store.id("total_debt", year),
                    value=None,
                    status="not_found",
                    unit="USD",
                    formula="debt_current + debt_noncurrent",
                    confidence=ConfidenceTier.NOT_FOUND,
                    notes=["total debt not computable: no total tag and no noncurrent debt"],
                )
            )
        cur_value = cur.value if (cur is not None and cur.value is not None) else 0.0
        if cur is None or cur.value is None:
            notes.append("current debt tag not found; treated as 0 in total")
        value = cur_value + non.value
        # Only count components that actually contributed a value toward confidence;
        # a deliberately-zeroed missing tag must not drag the tier to not_found.
        components = [c for c in (cur, non) if c is not None and c.value is not None]
        formula = "debt_current + debt_noncurrent"

    # Optional operating-lease inclusion (a nicety; off by default and flagged).
    if with_leases:
        lc = store.get("op_lease_liab_current", year)
        ln = store.get("op_lease_liab_noncurrent", year)
        lease_sum = sum(x.value for x in (lc, ln) if x is not None and x.value is not None)
        if lease_sum:
            value += lease_sum
            components += [x for x in (lc, ln) if x is not None]
            formula += " + operating lease liabilities"
            notes.append("operating leases INCLUDED (with_leases=True)")
    else:
        notes.append("operating leases excluded (default)")

    return store.add(
        ComputedMetric(
            name="total_debt",
            figure_id=store.id("total_debt", year),
            value=value,
            unit="USD",
            formula=formula,
            component_ids=[c.figure_id for c in components],
            confidence=weakest_tier(*components),
            notes=notes,
        )
    )


def compute_net_debt(store: FigureStore, year: int) -> ComputedMetric:
    """
    Net debt = total debt - cash - short-term investments.

    Net cash (negative net debt) is a real, strong state, not an error: we carry the
    negative value through and mark status="net_cash" so the scorecard reads it as the
    strongest leverage tier rather than a sign artifact (MSFT is net cash). A missing
    ST-investments tag is treated as 0 (commonly genuinely zero) with a note.
    """
    td = store.get("total_debt", year)
    cash = store.get("cash", year)
    sti = store.get("short_term_investments", year)

    if td is None or td.value is None or cash is None or cash.value is None:
        return store.add(
            ComputedMetric(
                name="net_debt",
                figure_id=store.id("net_debt", year),
                value=None,
                status="not_found",
                unit="USD",
                formula="total_debt - cash - short_term_investments",
                confidence=ConfidenceTier.NOT_FOUND,
                notes=["net debt not computable: missing total debt or cash"],
            )
        )

    notes: list[str] = []
    sti_value = sti.value if (sti is not None and sti.value is not None) else 0.0
    if sti is None or sti.value is None:
        notes.append("short-term investments not found; treated as 0")

    value = td.value - cash.value - sti_value
    status = "net_cash" if value < 0 else "ok"
    if status == "net_cash":
        notes.append("net cash position (cash + ST investments exceed total debt)")

    # ST investments, when missing, is treated as 0 and must not poison confidence.
    components = [c for c in (td, cash, sti) if c is not None and c.value is not None]
    return store.add(
        ComputedMetric(
            name="net_debt",
            figure_id=store.id("net_debt", year),
            value=value,
            status=status,
            unit="USD",
            formula="total_debt - cash - short_term_investments",
            component_ids=[c.figure_id for c in components],
            confidence=weakest_tier(*components),
            notes=notes,
        )
    )
