"""Fetches current price data for portfolio stocks using yfinance.

Provides live price fetching via the Yahoo Finance API (yfinance library)
and local JSON caching for offline / reproducible operation. Each ticker
returns: current price, daily change %, 52-week high, and 52-week low.

If a single ticker fails (e.g., delisted or API timeout), it is logged
and returned with None values — the rest of the portfolio still loads.
"""

import sys
import os

import pandas as pd
import yfinance as yf

# Allow imports from project root when this file is run directly
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
import config


def fetch_live_prices(tickers: list) -> pd.DataFrame:
    """Fetch live price data for the given tickers using yfinance.

    Uses yf.Tickers for a single batch request, then extracts per-ticker
    data from the .info dictionary. Calculates daily change % from the
    current price vs. previous close.

    Args:
        tickers: List of ticker symbol strings (e.g., ['AAPL', 'MSFT']).

    Returns:
        DataFrame with columns: ticker, company_name, price,
        daily_change_pct, 52w_high, 52w_low. Tickers that failed
        to fetch will have None values.
    """
    # Batch-fetch all tickers in a single API call
    data = yf.Tickers(" ".join(tickers))
    rows = []

    for ticker in tickers:
        try:
            info = data.tickers[ticker].info

            # Try multiple keys — yfinance varies by market hours
            current_price = (
                info.get("currentPrice") or info.get("regularMarketPrice")
            )
            prev_close = info.get("previousClose")
            high_52 = info.get("fiftyTwoWeekHigh")
            low_52 = info.get("fiftyTwoWeekLow")

            # Calculate daily change as a percentage
            if (
                current_price is not None
                and prev_close is not None
                and prev_close != 0
            ):
                daily_change = round(
                    (current_price - prev_close) / prev_close * 100, 2
                )
            else:
                daily_change = 0.0

            # Get the human-readable company name from our config
            stock_info = config.get_stock_by_ticker(ticker)
            company_name = (
                stock_info["company_name"] if stock_info else ticker
            )

            rows.append(
                {
                    "ticker": ticker,
                    "company_name": company_name,
                    "price": (
                        round(current_price, 2) if current_price else None
                    ),
                    "daily_change_pct": daily_change,
                    "52w_high": round(high_52, 2) if high_52 else None,
                    "52w_low": round(low_52, 2) if low_52 else None,
                }
            )

        except Exception as e:
            # If a single ticker fails, log the error and add a blank row
            stock_info = config.get_stock_by_ticker(ticker)
            company_name = (
                stock_info["company_name"] if stock_info else ticker
            )
            rows.append(
                {
                    "ticker": ticker,
                    "company_name": company_name,
                    "price": None,
                    "daily_change_pct": None,
                    "52w_high": None,
                    "52w_low": None,
                }
            )
            print(f"Warning: Could not fetch data for {ticker}: {e}")

    return pd.DataFrame(rows)


def save_prices_cache(df: pd.DataFrame, path: str) -> None:
    """Save a prices DataFrame to a local JSON cache file.

    The cache is used for reproducible demo runs (judges can see the
    same data without needing live market access).

    Args:
        df: DataFrame of price data to cache.
        path: File path to save the JSON cache.
    """
    df.to_json(path, orient="records", indent=2)


def load_prices_cache(path: str) -> pd.DataFrame:
    """Load a previously cached prices DataFrame from disk.

    Args:
        path: File path of the cached JSON data.

    Returns:
        DataFrame with cached price data matching the fetch_live_prices
        output format.
    """
    return pd.read_json(path)
