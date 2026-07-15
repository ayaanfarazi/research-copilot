"""
Phase 3, Step 1 — the brief assembler seam.

`assemble_brief` runs the exact Section B recipe from scripts/verify_panels.py
(deterministic financials → 10-K split → debt-footnote reconciliation → QoE bridge
→ six LLM panels) and bundles the result into one self-describing, Pydantic
`Brief` object. Everything the UI will render lives on that object; the UI never
re-runs the pipeline, it only reads a Brief.

The whole thing round-trips through JSON (`model_dump_json`/`model_validate_json`)
because every component — CompanyFinancials, FilingDocument, and all six panels —
is already Pydantic. That is what makes the disk cache below a one-liner and what
lets the eventual Streamlit app be a pure view over a cached artifact.

Assembly is expensive (real Anthropic calls for six panels). The disk cache under
data/cache/brief/{TICKER}_FY{year}.json means the app renders a company the second
time with zero API calls; use_cache=False forces a full rebuild and overwrites it.
"""

from __future__ import annotations

import types
from pathlib import Path
from typing import Literal, Union, get_args, get_origin

from annotated_types import MinLen
from pydantic import BaseModel, ValidationError
from pydantic.fields import FieldInfo

from config import CACHE_DIR, DEMO_PINS
from src.data.models import CompanyFinancials
from src.documents.fetch import fetch_and_split_latest_10k
from src.documents.models import FilingDocument
from src.llm.panels.addback_adversary import generate_addback_adversary
from src.llm.panels.business import generate_business_summary
from src.llm.panels.qoe_candidates import generate_qoe_candidates
from src.llm.panels.revenue_drivers import generate_revenue_drivers
from src.llm.panels.risks import generate_risks
from src.llm.panels.synthesis import generate_anchored_synthesis
from src.llm.schemas.addback_adversary import AddBackAdversaryPanel
from src.llm.schemas.descriptive import (
    BusinessSummaryPanel,
    QoECandidatesPanel,
    RevenueDriversPanel,
    RisksPanel,
)
from src.llm.schemas.synthesis import AnchoredSynthesisPanel
from src.metrics.qoe import build_qoe_bridge_from_figures
from src.pipeline import build_financials

# Assembled briefs live in their own cache namespace so they never collide with
# the flat companyfacts/document blob cache that also lives under data/cache.
BRIEF_CACHE_DIR = CACHE_DIR / "brief"


# ---------------------------------------------------------------------------
# Brief bundle
# ---------------------------------------------------------------------------

class PanelValidation(BaseModel):
    """The small per-panel validation summary the UI badges each panel with.

    `status` is the panel's own self-reported outcome (ok / validation_failed /
    confidence_gap); `violation_count` is len(ValidationResult.violations) at
    generation time. Kept next to the panel so the view never has to re-validate.
    """

    status: str
    violation_count: int


# One concrete envelope per panel type. They are near-identical but explicit so
# the Brief round-trips through JSON without any generic-parametrization ambiguity.
class BusinessSummaryEnvelope(BaseModel):
    panel: BusinessSummaryPanel
    validation: PanelValidation


class RisksEnvelope(BaseModel):
    panel: RisksPanel
    validation: PanelValidation


class RevenueDriversEnvelope(BaseModel):
    panel: RevenueDriversPanel
    validation: PanelValidation


class QoECandidatesEnvelope(BaseModel):
    panel: QoECandidatesPanel
    validation: PanelValidation


class SynthesisEnvelope(BaseModel):
    panel: AnchoredSynthesisPanel
    validation: PanelValidation


class AddBackAdversaryEnvelope(BaseModel):
    panel: AddBackAdversaryPanel
    validation: PanelValidation


class Brief(BaseModel):
    """Everything the UI renders for one company, in one round-trippable object."""

    ticker: str
    entity_name: str
    fiscal_year: int                 # anchor year = fin.fiscal_years[-1]
    fin: CompanyFinancials
    doc: FilingDocument

    # The six panels. Optional so a partially-assembled Brief is still a valid
    # object (the UI degrades a missing panel rather than crashing).
    business_summary: BusinessSummaryEnvelope | None = None
    risks: RisksEnvelope | None = None
    revenue_drivers: RevenueDriversEnvelope | None = None
    qoe_candidates: QoECandidatesEnvelope | None = None
    synthesis: SynthesisEnvelope | None = None
    addback_adversary: AddBackAdversaryEnvelope | None = None


# Panel name → (generator, envelope class). Mirrors the Section B panel set exactly.
_PANEL_SPECS = [
    ("business_summary", generate_business_summary, BusinessSummaryEnvelope),
    ("risks", generate_risks, RisksEnvelope),
    ("revenue_drivers", generate_revenue_drivers, RevenueDriversEnvelope),
    ("qoe_candidates", generate_qoe_candidates, QoECandidatesEnvelope),
    ("synthesis", generate_anchored_synthesis, SynthesisEnvelope),
    ("addback_adversary", generate_addback_adversary, AddBackAdversaryEnvelope),
]


# ---------------------------------------------------------------------------
# Panel normalization
# ---------------------------------------------------------------------------
#
# On a structural parse error, structured_call returns schema.model_construct(
# status="validation_failed") — a shell with the required content fields left
# UNSET (src/llm/client.py::_parse_error_shell). That is fine for the verify_panels
# gate, which never serializes, but such a shell cannot round-trip through JSON:
# model_dump drops the unset fields, and model_validate then rejects the reload.
# Since the Brief must round-trip, we normalize any shell into a *valid, empty*
# panel of the same type — status preserved, content fields filled with their
# type-appropriate empties. Complete panels pass through untouched.

def _min_length(field: FieldInfo) -> int:
    for meta in field.metadata:
        if isinstance(meta, MinLen):
            return meta.min_length
    return 0


def _empty_for(annotation: object, min_len: int = 0) -> object:
    """A minimal valid value for `annotation`, honoring a list min_length."""
    origin = get_origin(annotation)
    args = get_args(annotation)

    if origin in (Union, types.UnionType):
        if type(None) in args:
            return None
        return _empty_for(args[0])
    if origin is Literal:
        return args[0]
    if origin in (list, set, frozenset, tuple):
        elem = args[0] if args else str
        return [_empty_for(elem) for _ in range(max(min_len, 0))]
    if origin is dict:
        return {}
    if annotation is str:
        return ""
    if annotation is int:
        return 0
    if annotation is float:
        return 0.0
    if annotation is bool:
        return False
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return _empty_model_data(annotation)
    return None


def _empty_model_data(cls: type[BaseModel]) -> dict:
    """Build a dict of empties for every *required* field of `cls` (recursive)."""
    data: dict = {}
    for name, field in cls.model_fields.items():
        if field.is_required():
            data[name] = _empty_for(field.annotation, _min_length(field))
    return data


def _normalize_panel(panel: BaseModel) -> BaseModel:
    """Return a JSON-round-trippable instance of `type(panel)`.

    Complete panels are returned unchanged (re-validated to prove it). A parse-error
    shell (only `status` set) is rebuilt as a valid empty panel of the same type.
    """
    cls = type(panel)
    data = panel.model_dump()
    try:
        return cls.model_validate(data)
    except ValidationError:
        base = _empty_model_data(cls)
        base.update(data)  # keep whatever the shell actually set (e.g. status)
        return cls.model_validate(base)


# ---------------------------------------------------------------------------
# Disk cache
# ---------------------------------------------------------------------------

def _cache_path(ticker: str, year: int) -> Path:
    return BRIEF_CACHE_DIR / f"{ticker.upper()}_FY{year}.json"


def _load_cached_brief(ticker: str) -> Brief | None:
    """Return a cached Brief for `ticker` (no pipeline, no API), or None.

    The anchor year is not known until financials are built, so we glob the
    ticker's brief files and take the highest fiscal year. This keeps the
    cache-hit path free of any network or companyfacts work.
    """
    if not BRIEF_CACHE_DIR.exists():
        return None
    matches = sorted(BRIEF_CACHE_DIR.glob(f"{ticker.upper()}_FY*.json"))
    if not matches:
        return None
    return Brief.model_validate_json(matches[-1].read_text())


def _write_cached_brief(brief: Brief) -> Path:
    BRIEF_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(brief.ticker, brief.fiscal_year)
    path.write_text(brief.model_dump_json(indent=2))
    return path


# ---------------------------------------------------------------------------
# Assembly (the Section B recipe)
# ---------------------------------------------------------------------------

def assemble_brief(ticker: str, use_cache: bool = True) -> Brief:
    """Assemble the full Brief for `ticker` following verify_panels.section_b.

    use_cache=True returns a cached Brief when one exists (zero API calls).
    use_cache=False forces a full rebuild and overwrites the cache.
    """
    ticker = ticker.upper()

    if use_cache:
        cached = _load_cached_brief(ticker)
        if cached is not None:
            return cached

    pin = DEMO_PINS.get(ticker)

    # 1) Deterministic financials, then the split 10-K.
    fin0 = build_financials(ticker, as_of_fy=pin)
    doc = fetch_and_split_latest_10k(ticker, fin0)

    # 2) Reconcile the maturity wall against the real debt footnote when the
    #    splitter found one; companyfacts is disk-cached so this second build is
    #    cheap. If no footnote text, keep fin0 and let the wall stay a proxy.
    footnote_text = doc.sections.get("debt_footnote", "") or ""
    if footnote_text.strip():
        fin = build_financials(ticker, as_of_fy=pin, footnote_text=footnote_text)
    else:
        fin = fin0

    year = fin.fiscal_years[-1]

    # 3) Register QoE bridge figures into fin.figures before the panels build
    #    their allowlist (mutates fin.figures in place, exactly as Section B does).
    build_qoe_bridge_from_figures(ticker, fin.figures, year)

    # 4) Run all six panels.
    envelopes: dict[str, BaseModel] = {}
    for name, gen_fn, envelope_cls in _PANEL_SPECS:
        panel, vr = gen_fn(fin, doc, year)
        panel = _normalize_panel(panel)
        envelopes[name] = envelope_cls(
            panel=panel,
            validation=PanelValidation(
                status=panel.status,
                violation_count=len(vr.violations),
            ),
        )

    brief = Brief(
        ticker=ticker,
        entity_name=fin.entity_name,
        fiscal_year=year,
        fin=fin,
        doc=doc,
        **envelopes,
    )

    _write_cached_brief(brief)
    return brief
