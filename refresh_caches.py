"""Refresh all data caches for the dashboard.

Run this script whenever you want fresh data:
    python refresh_caches.py

It fetches news (Google News RSS), SEC filings (EDGAR), and prices
(yfinance), saving everything to data/cache/ so the dashboard can
load quickly.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import config
from src.ingestion.news import fetch_all_news, fetch_all_sector_news, fetch_mover_news_rss
from src.ingestion.edgar import fetch_all_portfolio_filings
from src.ingestion.prices import fetch_live_prices, save_prices_cache

CACHE_DIR = os.path.join(os.path.dirname(__file__), "data", "cache")
os.makedirs(CACHE_DIR, exist_ok=True)


def _identify_movers(threshold_pct: float = 3.0) -> list:
    """Identify portfolio stocks with daily price changes exceeding threshold.

    Reads the prices cache to find stocks moving more than ±threshold_pct
    in the current session, then enriches with company metadata from config.

    Args:
        threshold_pct: Absolute % change threshold (default 3.0).

    Returns:
        List of mover dicts with keys: ticker, company_name, sector,
        daily_change_pct.
    """
    prices_path = os.path.join(CACHE_DIR, "prices_cache.json")
    movers = []

    if not os.path.exists(prices_path):
        return movers

    try:
        import pandas as pd
        df = pd.read_json(prices_path)
        if "daily_change_pct" not in df.columns:
            return movers

        big_movers = df[df["daily_change_pct"].abs() >= threshold_pct]
        for _, row in big_movers.iterrows():
            ticker = row.get("ticker", "")
            stock_info = config.get_stock_by_ticker(ticker)
            if stock_info:
                movers.append({
                    "ticker": ticker,
                    "company_name": stock_info.get("company_name", ticker),
                    "sector": stock_info.get("sector", ""),
                    "daily_change_pct": float(row["daily_change_pct"]),
                })
    except Exception as e:
        print(f"  Warning: Could not identify movers: {e}")

    if movers:
        print(f"  Identified {len(movers)} significant movers (>{threshold_pct}%):")
        for m in movers:
            print(f"    {m['ticker']}: {m['daily_change_pct']:+.2f}%")

    return movers


def refresh_news():
    """Fetch news for all tickers (up to 20 per ticker) and save to cache.

    Sources: Google News RSS + Yahoo Finance RSS per ticker.
    Also runs tiered enhanced searches for movers:
      >±3%: 4 queries  |  >±4%: +3 catalyst queries  |  >±5%: +3 sector queries
    """
    print("\n=== Refreshing news cache (Google + Yahoo Finance RSS) ===")
    tickers = config.get_all_tickers()
    portfolio = config._get_portfolio()
    articles = fetch_all_news(tickers, portfolio=portfolio)

    # Tiered enhanced search for significant movers
    movers = _identify_movers(threshold_pct=3.0)
    if movers:
        tier_counts = {
            "3%+": sum(1 for m in movers if abs(m["daily_change_pct"]) < 4),
            "4%+": sum(1 for m in movers if 4 <= abs(m["daily_change_pct"]) < 5),
            "5%+": sum(1 for m in movers if abs(m["daily_change_pct"]) >= 5),
        }
        tier_str = ", ".join(f"{v}x {k}" for k, v in tier_counts.items() if v)
        print(f"\n=== Tiered mover search: {len(movers)} stocks ({tier_str}) ===")
        mover_articles = fetch_mover_news_rss(movers)
        if mover_articles:
            # Merge mover articles into the main list and re-dedup
            from src.ingestion.news import _normalise_title, _find_cluster
            from datetime import timezone, timedelta
            from email.utils import parsedate_to_datetime

            accepted_norms = [_normalise_title(a["title"]) for a in articles]
            added = 0
            for article in mover_articles:
                norm = _normalise_title(article.get("title", ""))
                if not norm:
                    continue
                cluster_idx = _find_cluster(norm, accepted_norms)
                if cluster_idx == -1:
                    article["mention_count"] = 1
                    articles.append(article)
                    accepted_norms.append(norm)
                    added += 1
                else:
                    articles[cluster_idx]["mention_count"] = (
                        articles[cluster_idx].get("mention_count", 1) + 1
                    )
                    # Upgrade centrality if mover search found primary
                    if article.get("entity_centrality") == "primary":
                        articles[cluster_idx]["entity_centrality"] = "primary"
            print(f"  Mover search added {added} new unique articles")

    path = os.path.join(CACHE_DIR, "news_cache.json")
    with open(path, "w") as f:
        json.dump(articles, f, indent=2, default=str)
    print(f"  Saved {len(articles)} unique articles to {path}")
    return articles


def refresh_sector_news():
    """Fetch sector-level news for all portfolio sectors and save to cache."""
    print("\n=== Refreshing sector news cache ===")
    sectors = config.get_all_sectors()
    articles = fetch_all_sector_news(sectors)
    path = os.path.join(CACHE_DIR, "sector_news_cache.json")
    with open(path, "w") as f:
        json.dump(articles, f, indent=2, default=str)
    print(f"  Saved {len(articles)} unique sector articles to {path}")
    return articles


def refresh_filings():
    """Fetch SEC filings for all tickers and save to cache."""
    print("\n=== Refreshing filings cache ===")
    tickers = config.get_all_tickers()
    filings = fetch_all_portfolio_filings(tickers)
    path = os.path.join(CACHE_DIR, "filings_cache.json")
    with open(path, "w") as f:
        json.dump(filings, f, indent=2, default=str)
    print(f"  Saved {len(filings)} filings to {path}")
    return filings


def refresh_prices():
    """Fetch live prices for all tickers and save to cache."""
    print("\n=== Refreshing prices cache ===")
    tickers = config.get_all_tickers()
    df = fetch_live_prices(tickers)
    path = os.path.join(CACHE_DIR, "prices_cache.json")
    save_prices_cache(df, path)
    print(f"  Saved prices for {len(df)} tickers to {path}")
    return df


def clear_event_caches():
    """Delete stale proactive/reactive event caches so they regenerate."""
    print("\n=== Clearing event caches ===")
    for name in ("proactive_cache.json", "reactive_cache.json", "daily_reactive_cache.json", "sector_cache.json"):
        path = os.path.join(CACHE_DIR, name)
        if os.path.exists(path):
            os.remove(path)
            print(f"  Deleted {path}")
        else:
            print(f"  {name} — already absent")


if __name__ == "__main__":
    refresh_news()
    refresh_sector_news()
    refresh_filings()
    refresh_prices()
    clear_event_caches()
    print("\n=== All caches refreshed! ===")
    print("Now run: streamlit run app.py")
    print("Make sure 'Use Cached Data' is OFF in the sidebar to trigger")
    print("the new AI-powered event pipeline.")
