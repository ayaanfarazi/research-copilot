from src.sec.client import get_json
import config


def get_cik(ticker: str) -> str:
    """
    Resolve a US ticker symbol to a zero-padded 10-digit CIK string.

    EDGAR's master ticker file maps every listed company to its CIK.
    We fetch it once (cached), scan for the ticker, and return the
    zero-padded CIK that all other EDGAR APIs expect.

    Args:
        ticker: Ticker symbol, e.g. "MSFT". Case-insensitive.

    Returns:
        10-digit zero-padded CIK string, e.g. "0000789019".

    Raises:
        ValueError: If the ticker isn't found in EDGAR's database.
    """
    data = get_json(config.SEC_TICKERS_URL)

    ticker_upper = ticker.upper()

    # The tickers file is a dict of {"0": {cik, ticker, title}, "1": {...}, ...}
    for entry in data.values():
        if entry["ticker"].upper() == ticker_upper:
            # CIK is stored as an integer; EDGAR APIs need it zero-padded to 10 digits.
            return str(entry["cik_str"]).zfill(10)

    raise ValueError(
        f"Ticker '{ticker}' not found in SEC EDGAR. "
        f"Check the symbol or try the full legal name."
    )
