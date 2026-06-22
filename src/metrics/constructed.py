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

# Strength rank (lower = stronger) for capping confidence on an indeterminate result.
_TIER_RANK = {
    ConfidenceTier.VERIFIED: 0,
    ConfidenceTier.HIGH: 1,
    ConfidenceTier.LOW: 2,
    ConfidenceTier.NOT_FOUND: 3,
}


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
    if not years:  # defensive: callers should never pass an empty window (see pipeline B1 guard)
        return {"method": "composed", "ref_components": set()}
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


_DEBT_REL_TOL = 0.01  # 1% band for "tag A ≈ tag B" reconciliation


def _reconcile_total_debt(total, nc, cur):
    """
    Verify a scope="total" tag actually IS a total, and correct it if it isn't (B4).

    Some filers tag `LongTermDebt` as the NONCURRENT-only balance (it equals
    `LongTermDebtNoncurrent`) while reporting the current portion separately -- e.g.
    McDonald's. Trusting the scope label then UNDER-states total debt at high confidence.
    We disambiguate against the noncurrent and current tags:

    Returns (value, formula, components, notes, ambiguous).
    """
    tv = total.value
    ncv = nc.value if (nc is not None and nc.value is not None) else None
    curv = cur.value if (cur is not None and cur.value is not None) else None

    if ncv is None:
        # No noncurrent tag to cross-check against -> trust the total tag as-is.
        return tv, "debt_total", [total], [], False

    approx_nc = abs(tv - ncv) <= _DEBT_REL_TOL * max(abs(tv), abs(ncv), 1.0)

    if approx_nc and curv is not None and curv > 0 and tv < ncv + curv:
        # Only add a separate current portion when it represents balance-sheet
        # near-term maturities (DebtCurrent / ShortTermBorrowings / lease-current tags).
        # LongTermDebtCurrent ALONE, with tv ≈ ncv, often captures sub-components
        # reclassified INTO the long-term line (MCD FY2025 10-K: $725M reclassified
        # to long-term; BS current-maturities line is a dash — not additive).
        cur_tag = cur.tag if cur is not None else None
        if cur_tag == "LongTermDebtCurrent":
            return (
                tv,
                "debt_total",
                [total],
                [f"{total.tag} ≈ {nc.tag}; {cur_tag} present but reclassified into "
                 f"long-term (not added on top of {total.tag})"],
                False,
            )
        return (
            tv + curv,
            "debt_total(reclassified noncurrent-only) + debt_current",
            [total, cur],
            [f"{total.tag} == {nc.tag} but a current portion exists ({cur.tag}); treated "
             f"{total.tag} as noncurrent-only and ADDED the current portion (corrected, not double-counted)"],
            False,
        )
    if curv is not None and abs(tv - (ncv + curv)) <= _DEBT_REL_TOL * max(abs(tv), 1.0):
        # Genuine total (≈ noncurrent + current), e.g. AAPL/INTC -> leave as-is.
        return tv, "debt_total", [total], [f"{total.tag} confirmed as a true total (≈ noncurrent + current)"], False
    if approx_nc and (curv is None or curv == 0):
        # Looks noncurrent-only but no current tag to add -> use as-is, note the limit.
        return tv, "debt_total", [total], [f"{total.tag} ≈ {nc.tag}; no current-debt tag found, used as-is"], False
    # Can't reconcile -> keep the total tag but flag and lower confidence.
    return tv, "debt_total", [total], [f"could not reconcile {total.tag} against noncurrent+current; used as-is"], True


def compute_total_debt(
    store: FigureStore, year: int, *, is_reit: bool = False, with_leases: bool = False
) -> ComputedMetric:
    """
    Total debt, scope-aware so overlapping tags are never summed (§7).

    Preference: a single `debt_total` tag if present (verified/corrected for scope via
    _reconcile_total_debt, B4); else current_only + noncurrent_only. A missing
    current-debt tag is treated as 0 (companies with no current maturities legitimately
    omit it) but the assumption is written into notes -- not silent. Operating leases
    are excluded by default; `with_leases=True` adds them.
    """
    notes: list[str] = []
    ambiguous = False
    total = store.get("debt_total", year)

    # REITs often tag only the senior-notes line as NotesPayable (~$23B for O) while
    # total debt per the 10-K footnote includes term loans, CP, mortgage, etc (~$26B).
    # A partial number is worse than honest abstention (B5a).
    if is_reit and total is not None and total.tag == "NotesPayable":
        return store.add(
            ComputedMetric(
                name="total_debt",
                figure_id=store.id("total_debt", year),
                value=None,
                status="not_found",
                unit="USD",
                formula="debt_total",
                component_ids=[total.figure_id],
                confidence=ConfidenceTier.NOT_FOUND,
                notes=[
                    "total debt not reported: NotesPayable is notes-only for this REIT, "
                    "not consolidated total debt (see 10-K debt footnote)"
                ],
            )
        )

    if total is not None and total.value is not None:
        nc_chk = store.get("debt_noncurrent", year)
        cur_chk = store.get("debt_current", year)
        value, formula, components, dnotes, ambiguous = _reconcile_total_debt(total, nc_chk, cur_chk)
        notes += dnotes
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

    conf = weakest_tier(*components)
    if ambiguous and _TIER_RANK[conf] < _TIER_RANK[ConfidenceTier.LOW]:
        conf = ConfidenceTier.LOW  # indeterminate scope reconciliation -> cap trust at LOW
    return store.add(
        ComputedMetric(
            name="total_debt",
            figure_id=store.id("total_debt", year),
            value=value,
            unit="USD",
            formula=formula,
            component_ids=[c.figure_id for c in components],
            confidence=conf,
            notes=notes,
        )
    )


def compute_total_debt_incl_leases(store: FigureStore, year: int) -> ComputedMetric:
    """
    Option D: total debt plus operating and finance lease liabilities.

    Keeps `total_debt` as debt-tags-only; this metric adds lease liabilities explicitly.
    When the lease-inclusive noncurrent tag won't reconcile with the debt total (HD/AAL/MPLX
    pattern), confidence is capped LOW and the note names the mismatch.
    """
    td = store.get("total_debt", year)
    if td is None or td.value is None:
        return store.add(
            ComputedMetric(
                name="total_debt_incl_leases",
                figure_id=store.id("total_debt_incl_leases", year),
                value=None,
                status="not_found",
                unit="USD",
                formula="total_debt + operating lease liabilities + finance lease liabilities",
                confidence=ConfidenceTier.NOT_FOUND,
                notes=["total_debt_incl_leases not computable: total_debt missing"],
            )
        )

    value = td.value
    components: list = [td]
    lease_parts: list[str] = []
    for concept, label in (
        ("finance_lease_liab_current", "finance lease current"),
        ("finance_lease_liab_noncurrent", "finance lease noncurrent"),
        ("op_lease_liab_current", "operating lease current"),
        ("op_lease_liab_noncurrent", "operating lease noncurrent"),
    ):
        fig = store.get(concept, year)
        if fig is not None and fig.value is not None and fig.value > 0:
            value += fig.value
            components.append(fig)
            lease_parts.append(label)

    notes: list[str] = ["Option D: total_debt (debt tags only) + explicit lease liabilities"]
    if lease_parts:
        notes.append("lease liabilities added: " + ", ".join(lease_parts))
    else:
        notes.append("no lease liability tags found; equals total_debt")

    reconcile_low = any("could not reconcile" in n for n in (td.notes or []))
    nc = store.get("debt_noncurrent", year)
    if (
        not reconcile_low
        and nc is not None
        and nc.tag == "LongTermDebtAndCapitalLeaseObligations"
        and nc.value is not None
    ):
        cur = store.get("debt_current", year)
        curv = cur.value if (cur is not None and cur.value is not None) else 0.0
        raw = store.get("debt_total", year)
        if raw is not None and raw.value is not None:
            combined = nc.value + curv
            if abs(raw.value - combined) > _DEBT_REL_TOL * max(abs(raw.value), abs(combined), 1.0):
                if abs(raw.value - nc.value) > _DEBT_REL_TOL * max(abs(raw.value), abs(nc.value), 1.0):
                    reconcile_low = True
                    notes.append(
                        f"lease-inclusive noncurrent tag ({nc.tag}={nc.value:,.0f}) does not "
                        f"reconcile with total_debt ({raw.value:,.0f}); confidence LOW"
                    )

    conf = weakest_tier(*components)
    if reconcile_low and _TIER_RANK[conf] < _TIER_RANK[ConfidenceTier.LOW]:
        conf = ConfidenceTier.LOW

    return store.add(
        ComputedMetric(
            name="total_debt_incl_leases",
            figure_id=store.id("total_debt_incl_leases", year),
            value=value,
            unit="USD",
            formula="total_debt + finance lease liabilities + operating lease liabilities",
            component_ids=[c.figure_id for c in components],
            confidence=conf,
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
