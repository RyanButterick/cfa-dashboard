"""Test script for news ingestion via RSS."""

import sys
import os
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.ingestion.news import fetch_news_rss


def main():
    print("Fetching news via Google News RSS for AAPL and JPM...")
    print()

    articles = fetch_news_rss(["AAPL", "JPM"])

    print(f"\nTotal articles fetched: {len(articles)}\n")

    for i, article in enumerate(articles):
        print(f"{i + 1}. [{article['ticker']}] {article['title']}")
        print(f"   Source: {article['source']}")
        print(f"   Published: {article['published']}")
        print()

    # Save to cache
    cache_path = os.path.join(
        os.path.dirname(__file__), "..", "data", "cache", "news_cache.json"
    )
    with open(cache_path, "w") as fp:
        json.dump(articles, fp, indent=2)

    if os.path.exists(cache_path):
        print(f"Cache saved successfully to: {cache_path}")
    else:
        print(f"ERROR: Cache file was not created at {cache_path}")


if __name__ == "__main__":
    main()
