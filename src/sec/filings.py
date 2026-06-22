"""Locate and build URLs for 10-K filings from EDGAR submissions data."""

from __future__ import annotations

from dataclasses import dataclass


class FilingNotFoundError(Exception):
    def __init__(self, ticker: str, anchor_year: int) -> None:
        super().__init__(
            f"No 10-K or 10-K/A found for {ticker!r} with period_of_report year {anchor_year}"
        )
        self.ticker = ticker
        self.anchor_year = anchor_year


@dataclass
class FilingRecord:
    accession: str        # "0000789019-24-000003" (with dashes)
    primary_doc: str      # "msft-20240630.htm"
    filed_date: str       # "2024-07-30"
    period_of_report: str # "2024-06-30"
    form: str             # "10-K" or "10-K/A"


def find_10k_filing(submissions: dict, anchor_year: int, ticker: str = "") -> FilingRecord:
    """
    Find the 10-K whose period_of_report year matches anchor_year.

    SEAM 3: filters on int(periodOfReport[:4]) == anchor_year, then among
    matches takes the row with the latest filingDate (handles amendments).
    Raises FilingNotFoundError if no match — never falls through to an
    unfiltered result.
    """
    recent = submissions.get("filings", {}).get("recent", {})
    forms        = recent.get("form", [])
    accessions   = recent.get("accessionNumber", [])
    periods      = recent.get("reportDate", [])      # EDGAR uses "reportDate", not "periodOfReport"
    primary_docs = recent.get("primaryDocument", [])
    filing_dates = recent.get("filingDate", [])

    matches: list[FilingRecord] = []
    for form, accession, period, primary_doc, filed_date in zip(
        forms, accessions, periods, primary_docs, filing_dates
    ):
        if form not in ("10-K", "10-K/A"):
            continue
        if not period or len(period) < 4:
            continue
        if int(period[:4]) != anchor_year:
            continue
        matches.append(FilingRecord(
            accession=accession,
            primary_doc=primary_doc,
            filed_date=filed_date,
            period_of_report=period,
            form=form,
        ))

    if not matches:
        raise FilingNotFoundError(ticker, anchor_year)

    # Latest filingDate wins — handles 10-K/A amendments superseding the original.
    matches.sort(key=lambda r: r.filed_date)
    return matches[-1]


def build_archive_url(cik: str, record: FilingRecord) -> str:
    """
    Build the EDGAR archive URL for the primary filing document.

    The archive path uses the integer CIK (no leading zeros) and the
    accession number with dashes removed.
    """
    cik_int = int(cik)
    accession_clean = record.accession.replace("-", "")
    return (
        f"https://www.sec.gov/Archives/edgar/data/"
        f"{cik_int}/{accession_clean}/{record.primary_doc}"
    )
