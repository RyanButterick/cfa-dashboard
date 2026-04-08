"""Test script for LLM event summarisation."""

import sys
import os
import json

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

load_dotenv()

import config
from src.llm.summariser import summarise_event


def main():
    # Define a sample event
    sample_event = {
        "title": "Federal Reserve holds interest rates steady at 4.5%",
        "description": (
            "The FOMC voted unanimously to maintain the federal funds rate, "
            "citing balanced risks to employment and inflation."
        ),
    }

    # Get affected stocks from config
    affected_stocks = [
        config.get_stock_by_ticker("JPM"),
        config.get_stock_by_ticker("NEE"),
    ]

    print("=" * 60)
    print("Testing summarise_event()")
    print("=" * 60)
    print(f"Event: {sample_event['title']}")
    print(f"Affected stocks: JPM, NEE\n")

    summary = summarise_event(sample_event, affected_stocks)
    print(f"Summary:\n{summary}\n")

    # Save to cache
    cache_data = {
        "event": sample_event,
        "affected_stocks": [
            {"ticker": s["ticker"], "company_name": s["company_name"]}
            for s in affected_stocks
        ],
        "summary": summary,
    }

    cache_path = os.path.join(
        os.path.dirname(__file__), "..", "data", "cache", "llm_test_cache.json"
    )
    with open(cache_path, "w") as fp:
        json.dump(cache_data, fp, indent=2)

    if os.path.exists(cache_path):
        print(f"LLM cache saved successfully to: {cache_path}")
    else:
        print(f"ERROR: Cache file was not created at {cache_path}")


if __name__ == "__main__":
    main()
