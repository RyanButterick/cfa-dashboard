"""Test script for the event classification system."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.processing.classifier import classify_event, classify_all_events
from config import PORTFOLIO


def main():
    # Test 1: Direct match — mentions a specific company/ticker
    print("=" * 60)
    print("Test 1: Direct match — Apple/AAPL")
    print("=" * 60)
    event_direct = {
        "title": "Apple reports record Q2 earnings",
        "description": "AAPL beats analyst expectations with strong iPhone sales.",
    }
    result = classify_event(event_direct, PORTFOLIO)
    print(f"  Tier:    {result['relevance_tier']}")
    print(f"  Badge:   {result['relevance_badge']}")
    print(f"  Tickers: {result['affected_tickers']}")
    assert "AAPL" in result["affected_tickers"], "FAIL: AAPL should be in affected tickers"
    assert result["relevance_tier"] == "Direct", "FAIL: Should be Direct tier"
    print("  PASSED\n")

    # Test 2: Sector match — CPI data with affected_sectors
    print("=" * 60)
    print("Test 2: Sector match — US CPI data")
    print("=" * 60)
    event_sector = {
        "title": "US CPI rises above expectations",
        "description": "Consumer prices increased 0.4% in March, above the 0.3% forecast.",
        "affected_sectors": ["Consumer Staples", "Consumer Discretionary", "Financials"],
    }
    result = classify_event(event_sector, PORTFOLIO)
    print(f"  Tier:    {result['relevance_tier']}")
    print(f"  Badge:   {result['relevance_badge']}")
    print(f"  Tickers: {result['affected_tickers']}")
    assert result["relevance_tier"] == "Sector", "FAIL: Should be Sector tier"
    # Should include PG (Consumer Staples), AMZN (Consumer Discretionary), JPM (Financials)
    assert "PG" in result["affected_tickers"], "FAIL: PG should be in affected tickers"
    assert "JPM" in result["affected_tickers"], "FAIL: JPM should be in affected tickers"
    print("  PASSED\n")

    # Test 3: Macro match — broad market event
    print("=" * 60)
    print("Test 3: Macro match — Global market rally")
    print("=" * 60)
    event_macro = {
        "title": "Global markets rally on trade deal optimism",
        "description": "Stock futures rise broadly on reports of progress in trade negotiations.",
    }
    result = classify_event(event_macro, PORTFOLIO)
    print(f"  Tier:    {result['relevance_tier']}")
    print(f"  Badge:   {result['relevance_badge']}")
    print(f"  Tickers: {result['affected_tickers']}")
    assert result["relevance_tier"] == "Macro", "FAIL: Should be Macro tier"
    assert len(result["affected_tickers"]) == 10, "FAIL: Should affect all 10 tickers"
    print("  PASSED\n")

    # Test 4: Batch classification
    print("=" * 60)
    print("Test 4: Batch classification of all 3 events")
    print("=" * 60)
    all_results = classify_all_events(
        [event_direct, event_sector, event_macro], PORTFOLIO
    )
    tiers = [r["relevance_tier"] for r in all_results]
    print(f"  Tiers: {tiers}")
    assert tiers == ["Direct", "Sector", "Macro"], "FAIL: Batch order should be Direct, Sector, Macro"
    print("  PASSED\n")

    print("All classifier tests passed!")


if __name__ == "__main__":
    main()
