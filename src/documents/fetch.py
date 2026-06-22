"""Fetch and split the 10-K filing for a given company and anchor year."""

from __future__ import annotations

from src.data.models import CompanyFinancials
from src.documents.clean import html_to_text
from src.documents.models import FilingDocument
from src.documents.split import split_10k
from src.sec.client import get_submissions, get_filing_html
from src.sec.filings import build_archive_url, find_10k_filing


def fetch_and_split_latest_10k(
    ticker: str,
    financials: CompanyFinancials,
) -> FilingDocument:
    """
    Fetch and split the 10-K whose period_of_report matches the anchor year.

    anchor_year = financials.fiscal_years[-1]

    Returns a FilingDocument with sections and all offset fields populated.
    Raw HTML is cached to disk; cleaned/split text is derived on each call.
    """
    anchor_year = financials.fiscal_years[-1]
    cik = financials.cik

    submissions = get_submissions(cik)
    record = find_10k_filing(submissions, anchor_year, ticker=ticker)

    url = build_archive_url(cik, record)
    html = get_filing_html(url)

    text, heading_offsets = html_to_text(html)

    doc = FilingDocument(
        ticker=ticker,
        cik=cik,
        accession=record.accession,
        filed_date=record.filed_date,
        period_of_report=record.period_of_report,
        primary_doc=record.primary_doc,
    )

    split_10k(text, doc)

    return doc
