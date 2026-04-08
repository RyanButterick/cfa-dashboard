"""Test script for yfinance price data fetching and caching."""

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.ingestion.prices import fetch_live_prices, save_prices_cache
from config import get_all_tickers


def main():
    print("Fetching live prices for all portfolio tickers...")
    tickers = get_all_tickers()
    print(f"Tickers: {tickers}\n")

    df = fetch_live_prices(tickers)
    print(df.to_string(index=False))
    print(f"\nRows returned: {len(df)}")

    cache_path = os.path.join(os.path.dirname(__file__), "..", "data", "cache", "prices_cache.json")
    save_prices_cache(df, cache_path)

    if os.path.exists(cache_path):
        print(f"\nCache saved successfully to: {cache_path}")
    else:
        print(f"\nERROR: Cache file was not created at {cache_path}")


if __name__ == "__main__":
    main()
