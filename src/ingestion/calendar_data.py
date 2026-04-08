"""Loads and merges calendar data from multiple sources into a unified timeline.

Combines three types of events:
  1. Macro events — manually curated JSON file of economic releases
     (CPI, NFP, FOMC, GDP, etc.)
  2. Earnings dates — fetched from yfinance for each portfolio ticker
  3. SEC filings — loaded from the EDGAR cache (8-K, 10-K, 10-Q)

All events are normalised to a consistent schema with keys: event_name,
date, time, description, affected_sectors, source_url, event_type.
"""

import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
import config


def load_macro_calendar(path: str) -> list:
    """Load macroeconomic events from a local JSON file.

    The macro calendar is manually curated and committed to the repo
    at data/macro_calendar.json. It contains 15-20 events covering
    the next 30 days of key economic releases.

    Args:
        path: File path to the macro_calendar.json file.

    Returns:
        List of event dictionaries with date, event_name, description,
        and affected_sectors fields.

    Raises:
        FileNotFoundError: If the macro calendar file doesn't exist.
        json.JSONDecodeError: If the file contains invalid JSON.
    """
    try:
        with open(path, "r") as f:
            events = json.load(f)
        return events
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"Warning: Could not load macro calendar from {path}: {e}")
        return []


def fetch_earnings_dates(tickers: list) -> list:
    """Fetch upcoming earnings dates for the given tickers using yfinance.

    Queries the yfinance calendar API for each ticker. Handles both
    dict-format (newer yfinance versions) and DataFrame-format (older
    versions) responses.

    Args:
        tickers: List of ticker symbol strings (e.g., ['AAPL', 'MSFT']).

    Returns:
        List of earnings event dictionaries with keys: event_name, date,
        time, description, affected_sectors, source_url, event_type.
        Tickers without earnings dates or that fail to fetch are skipped.
    """
    import yfinance as yf

    earnings_events = []

    for ticker in tickers:
        try:
            stock = yf.Ticker(ticker)
            calendar = stock.calendar

            # Skip if no calendar data is available
            if calendar is None or (
                hasattr(calendar, "empty") and calendar.empty
            ):
                continue

            # Extract the earnings date — format varies by yfinance version
            earnings_date = None

            if isinstance(calendar, dict):
                # Newer yfinance versions return a dict
                earnings_date = calendar.get("Earnings Date")
                if isinstance(earnings_date, list) and len(earnings_date) > 0:
                    earnings_date = earnings_date[0]
            else:
                # Older versions return a DataFrame
                if "Earnings Date" in calendar.index:
                    earnings_date = calendar.loc["Earnings Date"].iloc[0]

            if earnings_date is not None:
                # Normalise date to YYYY-MM-DD string format
                if hasattr(earnings_date, "strftime"):
                    date_str = earnings_date.strftime("%Y-%m-%d")
                else:
                    date_str = str(earnings_date)[:10]

                # Look up company metadata from our portfolio config
                stock_info = config.get_stock_by_ticker(ticker)
                company_name = (
                    stock_info["company_name"] if stock_info else ticker
                )
                sector = stock_info["sector"] if stock_info else "Unknown"

                earnings_events.append(
                    {
                        "event_name": f"{ticker} Earnings Report",
                        "date": date_str,
                        "time": "TBC",
                        "description": (
                            f"Upcoming earnings report for {company_name}."
                        ),
                        "affected_sectors": [sector],
                        "source_url": "",
                        "event_type": "earnings",
                    }
                )
                print(f"  [{ticker}] Earnings date: {date_str}")

        except Exception as e:
            # Don't crash if one ticker fails — just skip it
            print(f"  [{ticker}] Could not fetch earnings date: {e}")
            continue

    return earnings_events


def fetch_dividend_dates(tickers: list) -> list:
    """Fetch upcoming and recent dividend dates for the given tickers.

    Queries yfinance for ex-dividend and dividend payment dates from
    the last 90 days and upcoming scheduled dates.

    Args:
        tickers: List of ticker symbol strings (e.g., ['AAPL', 'MSFT']).

    Returns:
        List of dividend event dictionaries with keys: event_name, date,
        time, description, affected_sectors, source_url, event_type.
    """
    import yfinance as yf
    from datetime import datetime, timedelta

    dividend_events = []
    cutoff = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")

    for ticker in tickers:
        try:
            stock = yf.Ticker(ticker)

            # Get company metadata
            stock_info_cfg = config.get_stock_by_ticker(ticker)
            company_name = (
                stock_info_cfg["company_name"] if stock_info_cfg else ticker
            )
            sector = stock_info_cfg["sector"] if stock_info_cfg else "Unknown"

            # --- Ex-dividend date from calendar ---
            calendar = stock.calendar
            ex_div_date = None

            if isinstance(calendar, dict):
                ex_div_date = calendar.get("Ex-Dividend Date")
            elif calendar is not None and hasattr(calendar, "index"):
                if "Ex-Dividend Date" in calendar.index:
                    ex_div_date = calendar.loc["Ex-Dividend Date"].iloc[0]

            if ex_div_date is not None:
                if hasattr(ex_div_date, "strftime"):
                    date_str = ex_div_date.strftime("%Y-%m-%d")
                else:
                    date_str = str(ex_div_date)[:10]

                dividend_events.append(
                    {
                        "event_name": f"{ticker} Ex-Dividend Date",
                        "date": date_str,
                        "time": "Market Open",
                        "description": (
                            f"Ex-dividend date for {company_name}. "
                            f"Shares must be owned before this date to "
                            f"receive the next dividend payment."
                        ),
                        "affected_sectors": [sector],
                        "source_url": "",
                        "event_type": "ex-dividend",
                    }
                )
                print(f"  [{ticker}] Ex-dividend date: {date_str}")

            # --- Dividend date (payment date) from calendar ---
            div_pay_date = None

            if isinstance(calendar, dict):
                div_pay_date = calendar.get("Dividend Date")
            elif calendar is not None and hasattr(calendar, "index"):
                if "Dividend Date" in calendar.index:
                    div_pay_date = calendar.loc["Dividend Date"].iloc[0]

            if div_pay_date is not None:
                if hasattr(div_pay_date, "strftime"):
                    pay_date_str = div_pay_date.strftime("%Y-%m-%d")
                else:
                    pay_date_str = str(div_pay_date)[:10]

                # Avoid adding if same as ex-div date
                if pay_date_str != (date_str if ex_div_date else ""):
                    dividend_events.append(
                        {
                            "event_name": f"{ticker} Dividend Payment",
                            "date": pay_date_str,
                            "time": "N/A",
                            "description": (
                                f"Dividend payment date for {company_name}."
                            ),
                            "affected_sectors": [sector],
                            "source_url": "",
                            "event_type": "dividend",
                        }
                    )
                    print(f"  [{ticker}] Dividend payment: {pay_date_str}")

            # --- Recent dividend history (last 90 days) ---
            try:
                dividends = stock.dividends
                if dividends is not None and not dividends.empty:
                    recent_divs = dividends[dividends.index >= cutoff]
                    for div_date, amount in recent_divs.items():
                        d_str = div_date.strftime("%Y-%m-%d")
                        # Skip if we already have this date from calendar
                        existing_dates = [
                            e["date"] for e in dividend_events
                            if ticker in e["event_name"]
                        ]
                        if d_str not in existing_dates:
                            dividend_events.append(
                                {
                                    "event_name": (
                                        f"{ticker} Dividend "
                                        f"(${amount:.4f}/share)"
                                    ),
                                    "date": d_str,
                                    "time": "N/A",
                                    "description": (
                                        f"{company_name} paid a dividend "
                                        f"of ${amount:.4f} per share."
                                    ),
                                    "affected_sectors": [sector],
                                    "source_url": "",
                                    "event_type": "dividend",
                                }
                            )
                            print(
                                f"  [{ticker}] Historical dividend: "
                                f"{d_str} (${amount:.4f})"
                            )
            except Exception:
                pass  # Dividend history not available for all tickers

        except Exception as e:
            print(f"  [{ticker}] Could not fetch dividend dates: {e}")
            continue

    return dividend_events


def merge_calendar_events(
    macro_events: list, earnings: list, filings: list, dividends: list = None
) -> list:
    """Merge and sort all calendar events into a unified timeline.

    Normalises all event types to a consistent set of keys so they
    can be displayed in a single table and calendar grid. Filing events
    are transformed from the EDGAR format to the standard schema.

    Args:
        macro_events: List of macroeconomic event dictionaries.
        earnings: List of earnings date dictionaries.
        filings: List of SEC filing event dictionaries (EDGAR format).
        dividends: List of dividend event dictionaries (optional).

    Returns:
        Sorted list of all events by date ascending. Each event has keys:
        event_name, date, time, description, affected_sectors, source_url,
        event_type.
    """
    if dividends is None:
        dividends = []
    merged = []

    # Add macro events (already in the standard format)
    for event in macro_events:
        merged.append(
            {
                "event_name": event.get("event_name", ""),
                "date": event.get("date", ""),
                "time": event.get("time", "TBC"),
                "description": event.get("description", ""),
                "affected_sectors": event.get("affected_sectors", []),
                "source_url": event.get("source_url", ""),
                "event_type": event.get("event_type", "macro"),
            }
        )

    # Add earnings events (already in the standard format)
    for event in earnings:
        merged.append(
            {
                "event_name": event.get("event_name", ""),
                "date": event.get("date", ""),
                "time": event.get("time", "TBC"),
                "description": event.get("description", ""),
                "affected_sectors": event.get("affected_sectors", []),
                "source_url": event.get("source_url", ""),
                "event_type": event.get("event_type", "earnings"),
            }
        )

    # Transform and add filing events from EDGAR format
    for filing in filings:
        ticker = filing.get("ticker", "")
        filing_type = filing.get("filing_type", "")
        stock_info = config.get_stock_by_ticker(ticker)
        sector = stock_info["sector"] if stock_info else "Unknown"

        merged.append(
            {
                "event_name": f"{ticker} {filing_type} Filing",
                "date": filing.get("filing_date", ""),
                "time": "N/A",
                "description": filing.get(
                    "description", f"{filing_type} filing by {ticker}"
                ),
                "affected_sectors": [sector],
                "source_url": filing.get("url", ""),
                "event_type": "filing",
            }
        )

    # Add dividend events (already in the standard format)
    for event in dividends:
        merged.append(
            {
                "event_name": event.get("event_name", ""),
                "date": event.get("date", ""),
                "time": event.get("time", "N/A"),
                "description": event.get("description", ""),
                "affected_sectors": event.get("affected_sectors", []),
                "source_url": event.get("source_url", ""),
                "event_type": event.get("event_type", "dividend"),
            }
        )

    # Sort chronologically by date
    merged.sort(key=lambda x: x.get("date", ""))

    return merged
