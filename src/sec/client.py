import time
import hashlib
import json
from pathlib import Path

import requests

import config

# SEC's published rate limit. Exceeding this gets your IP temporarily blocked.
_MAX_REQUESTS_PER_SECOND = 10
_MIN_INTERVAL = 1.0 / _MAX_REQUESTS_PER_SECOND  # 0.1s between calls

_last_request_time: float = 0.0


def _throttle() -> None:
    """Enforce the SEC rate limit by sleeping if we're calling too fast."""
    global _last_request_time
    elapsed = time.time() - _last_request_time
    if elapsed < _MIN_INTERVAL:
        time.sleep(_MIN_INTERVAL - elapsed)
    _last_request_time = time.time()


def _cache_path(url: str) -> Path:
    """Return a file path under data/cache/ keyed by a hash of the URL."""
    url_hash = hashlib.md5(url.encode()).hexdigest()
    return config.CACHE_DIR / f"{url_hash}.json"


def get_json(url: str, use_cache: bool = True) -> dict:
    """
    Fetch a URL from SEC EDGAR and return parsed JSON.

    Caches responses to disk by default so repeated runs during development
    don't re-hit the network. Set use_cache=False to force a fresh fetch.

    Args:
        url:       Full EDGAR URL to fetch.
        use_cache: If True, return cached response when available.

    Returns:
        Parsed JSON as a dict.

    Raises:
        requests.HTTPError: On 4xx/5xx responses, with a readable message.
                            A 403 almost always means the User-Agent header
                            is missing or malformed.
    """
    cache_file = _cache_path(url)

    if use_cache and cache_file.exists():
        with open(cache_file) as f:
            return json.load(f)

    _throttle()

    # SEC's terms require a descriptive User-Agent; generic strings get blocked.
    headers = {"User-Agent": config.SEC_USER_AGENT}
    response = requests.get(url, headers=headers, timeout=30)

    if response.status_code == 403:
        raise requests.HTTPError(
            f"SEC returned 403 Forbidden for {url}\n"
            f"Check that SEC_USER_AGENT in .env is set to a real name and email."
        )
    response.raise_for_status()

    data = response.json()

    if use_cache:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_file, "w") as f:
            json.dump(data, f)

    return data


def get_company_facts(cik: str) -> dict:
    """
    Fetch all XBRL facts for a company from the SEC companyfacts API.

    This returns every financial figure ever tagged in every filing —
    across all periods, amendments, and restatements. Phase 1 will deduplicate
    this down to one clean value per concept per fiscal year.

    Args:
        cik: 10-digit zero-padded CIK string, e.g. "0000789019".

    Returns:
        Raw companyfacts dict. Key structure:
            data["facts"]["us-gaap"][tag]["units"]["USD"] → list of fact dicts
        Each fact dict has: val, accn, fy, fp, form, filed, frame.
    """
    url = f"{config.SEC_BASE_URL}/api/xbrl/companyfacts/CIK{cik}.json"
    return get_json(url)


def get_submissions(cik: str) -> dict:
    """
    Fetch a company's submissions metadata from the SEC submissions API.

    Used for issuer-level facts that aren't in companyfacts: the SIC industry code
    (to detect banks/insurers whose credit panel we degrade rather than fake) and
    the reported fiscal-year-end.

    Args:
        cik: 10-digit zero-padded CIK string, e.g. "0000789019".

    Returns:
        Submissions dict. Useful keys: "sic", "sicDescription", "fiscalYearEnd".
    """
    url = f"{config.SEC_BASE_URL}/submissions/CIK{cik}.json"
    return get_json(url)
    