from pathlib import Path
from dotenv import load_dotenv
import os

load_dotenv()

def _require(key: str) -> str:
    """
    Fetch a required environment variable, failing loudly if it's missing.

    Raises a clear ValueError rather than letting a None bubble up into a
    cryptic AttributeError or HTTP 403 deep inside the app.
    """
    value = os.getenv(key)
    if not value:
        raise ValueError(
            f"Missing required environment variable: {key}\n"
            f"Add it to your .env file and restart."
        )
    return value

# Loaded once at import time; any missing key fails immediately.
ANTHROPIC_API_KEY: str = _require("ANTHROPIC_API_KEY")

# SEC requires a descriptive User-Agent on every request or returns 403.
# Format: "Name project-name email"
SEC_USER_AGENT: str = _require("SEC_USER_AGENT")

# Base URLs for the two SEC APIs we use.
SEC_BASE_URL = "https://data.sec.gov"
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"

# Model ID kept here so we can swap models without touching every call site.
ANTHROPIC_MODEL = "claude-sonnet-4-6"

CACHE_DIR = Path("data/cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Anchor year pins for the five demo companies. build_financials accepts an
# as_of_fy param; passing DEMO_PINS.get(ticker) locks the pipeline to the
# year where goldens are hand-verified. Non-demo tickers get None → latest.
DEMO_PINS: dict[str, int] = {
    "MSFT": 2024,
    "VZ":   2024,
    "MCD":  2024,
    "NVDA": 2024,
    "CRM":  2024,
}
