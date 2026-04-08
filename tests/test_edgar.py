"""Test script for SEC EDGAR filing data fetching."""

import sys
import os
import json

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.ingestion.edgar import fetch_recent_filings, fetch_all_portfolio_filings
from config import get_all_tickers


def main():
    # --- Test 1: Single ticker ---
    print("=" * 60)
    print("Test 1: Fetching recent filings for AAPL")
    print("=" * 60)
    filings = fetch_recent_filings("AAPL", ["8-K", "10-K", "10-Q"], 3)
    for f in filings:
        print(f"  {f['filing_date']} | {f['filing_type']} | {f['description']}")
        print(f"    URL: {f['url']}")
    print(f"\nFilings returned: {len(filings)}\n")

    # --- Test 2: All portfolio tickers ---
    print("=" * 60)
    print("Test 2: Fetching filings for all 10 portfolio tickers")
    print("=" * 60)
    tickers = get_all_tickers()
    all_filings = fetch_all_portfolio_filings(tickers)
    print(f"\nTotal filings found: {len(all_filings)}")

    # Show first 5 as a sample
    print("\nMost recent 5 filings across portfolio:")
    for f in all_filings[:5]:
        print(f"  {f['filing_date']} | {f['ticker']} | {f['filing_type']} | {f['description']}")

    # --- Save to cache ---
    cache_path = os.path.join(os.path.dirname(__file__), "..", "data", "cache", "filings_cache.json")
    with open(cache_path, "w") as fp:
        json.dump(all_filings, fp, indent=2)

    if os.path.exists(cache_path):
        print(f"\nCache saved successfully to: {cache_path}")
    else:
        print(f"\nERROR: Cache file was not created at {cache_path}")


if __name__ == "__main__":
    main()
