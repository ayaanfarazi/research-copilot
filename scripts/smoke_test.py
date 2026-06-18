"""
Phase 0 exit gate. Run with: python scripts/smoke_test.py MSFT

Checks:
  1. Config loads both required env vars.
  2. SEC ticker lookup returns a valid CIK.
  3. companyfacts fetch returns real XBRL data.
  4. Anthropic structured call returns valid JSON.

Exit 0 on full success, 1 on any failure.
"""
import sys
import json

# Add the project root to the path so imports work from any directory.
sys.path.insert(0, ".")


def run(ticker: str) -> bool:
    all_passed = True

    # --- Step 1: config ---
    print("[config] Loading environment variables...")
    try:
        import config
        print(f"[config] ✓ ANTHROPIC_API_KEY: set")
        print(f"[config] ✓ SEC_USER_AGENT: {config.SEC_USER_AGENT}")
    except ValueError as e:
        print(f"[config] ✗ {e}")
        return False  # Nothing else can run without config.

    # --- Step 2: ticker → CIK ---
    print(f"\n[SEC] Resolving ticker {ticker}...")
    try:
        from src.sec.ticker import get_cik
        cik = get_cik(ticker)
        print(f"[SEC] ✓ {ticker} → CIK {cik}")
    except Exception as e:
        print(f"[SEC] ✗ Ticker lookup failed: {e}")
        all_passed = False
        cik = None

    # --- Step 3: companyfacts fetch ---
    if cik:
        print(f"\n[SEC] Fetching companyfacts for CIK {cik}...")
        try:
            from src.sec.client import get_company_facts
            facts = get_company_facts(cik)

            company_name = facts.get("entityName", "unknown")
            us_gaap_tags = facts.get("facts", {}).get("us-gaap", {})
            tag_count = len(us_gaap_tags)

            # Print one sample tag to show we can read the data shape.
            sample_tag = next(iter(us_gaap_tags), None)
            if sample_tag:
                units = us_gaap_tags[sample_tag].get("units", {})
                unit_key = next(iter(units), None)
                period_count = len(units[unit_key]) if unit_key else 0
                sample_info = f"{sample_tag} — {period_count} reported periods"
            else:
                sample_info = "no tags found"

            print(f"[SEC] ✓ Company: {company_name}")
            print(f"[SEC] ✓ us-gaap tags available: {tag_count}")
            print(f"[SEC] ✓ Sample tag: {sample_info}")
        except Exception as e:
            print(f"[SEC] ✗ companyfacts fetch failed: {e}")
            all_passed = False

    # --- Step 4: Anthropic smoke call ---
    print(f"\n[LLM] Calling Anthropic API...")
    try:
        from src.llm.client import smoke_call
        result = smoke_call()
        print(f"[LLM] ✓ Response: {result.model_dump()}")
    except Exception as e:
        print(f"[LLM] ✗ Anthropic call failed: {e}")
        all_passed = False

    # --- Summary ---
    print()
    if all_passed:
        print("✓ Phase 0 smoke test passed")
    else:
        print("✗ Phase 0 smoke test FAILED — see errors above")

    return all_passed


if __name__ == "__main__":
    ticker = sys.argv[1] if len(sys.argv) > 1 else "MSFT"
    success = run(ticker)
    sys.exit(0 if success else 1)
