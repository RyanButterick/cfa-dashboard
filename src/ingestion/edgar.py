"""Fetches recent SEC EDGAR filings (8-K, 10-K, 10-Q) for portfolio stocks.

Uses the SEC EDGAR API with required User-Agent header for rate limiting.
All external API calls are wrapped in try/except to ensure graceful fallback.
"""

import time
import json

import requests

# SEC requires a User-Agent header identifying the requester
HEADERS = {"User-Agent": "CFA Dashboard ryanbutterick04@gmail.com"}

# Module-level cache for the ticker-to-CIK mapping so we only download it once
_ticker_cik_map = None


def _load_ticker_cik_map() -> dict:
    """Download and cache the SEC ticker-to-CIK mapping.

    Downloads the SEC company tickers JSON file which maps every public
    company's ticker symbol to its Central Index Key (CIK). The CIK is
    needed to query EDGAR for filings.

    Returns:
        Dictionary mapping uppercase ticker strings to zero-padded CIK strings.
        Returns an empty dict if the download fails.
    """
    global _ticker_cik_map
    if _ticker_cik_map is not None:
        return _ticker_cik_map

    url = "https://www.sec.gov/files/company_tickers.json"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, json.JSONDecodeError) as e:
        print(f"Warning: Could not download SEC CIK mapping: {e}")
        _ticker_cik_map = {}
        return _ticker_cik_map

    _ticker_cik_map = {}
    for entry in data.values():
        ticker = entry["ticker"].upper()
        cik = str(entry["cik_str"]).zfill(10)
        _ticker_cik_map[ticker] = cik

    return _ticker_cik_map


def fetch_recent_filings(
    ticker: str,
    filing_types: list | None = None,
    count: int = 5,
) -> list:
    """Fetch recent SEC filings for a single ticker from EDGAR.

    Queries the EDGAR submissions API for the given company and filters
    results to the requested filing types (e.g., 8-K, 10-K, 10-Q).

    Args:
        ticker: Stock ticker symbol (e.g., "AAPL").
        filing_types: List of filing type strings to filter on.
            Defaults to ["8-K", "10-K", "10-Q"].
        count: Maximum number of filings to retrieve.

    Returns:
        List of filing dictionaries with keys: ticker, filing_type,
        filing_date, description, url. Returns an empty list on error.
    """
    if filing_types is None:
        filing_types = ["8-K", "10-K", "10-Q"]

    cik_map = _load_ticker_cik_map()
    ticker_upper = ticker.upper()

    if ticker_upper not in cik_map:
        print(f"Warning: Ticker '{ticker}' not found in SEC CIK mapping.")
        return []

    cik = cik_map[ticker_upper]

    # Fetch submissions for this CIK
    submissions_url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    try:
        resp = requests.get(submissions_url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, json.JSONDecodeError) as e:
        print(f"Warning: Could not fetch EDGAR submissions for {ticker}: {e}")
        return []

    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    primary_docs = recent.get("primaryDocument", [])
    accession_numbers = recent.get("accessionNumber", [])
    descriptions = recent.get("primaryDocDescription", [])

    results = []
    filing_types_upper = [ft.upper() for ft in filing_types]

    for i in range(len(forms)):
        if forms[i].upper() in filing_types_upper:
            # Build the EDGAR viewer URL from the accession number
            accession_no_dashes = accession_numbers[i].replace("-", "")
            filing_url = (
                f"https://www.sec.gov/Archives/edgar/data/"
                f"{cik.lstrip('0')}/{accession_no_dashes}/{primary_docs[i]}"
            )

            results.append(
                {
                    "ticker": ticker_upper,
                    "filing_type": forms[i],
                    "filing_date": dates[i],
                    "description": (
                        descriptions[i] if i < len(descriptions) else ""
                    ),
                    "url": filing_url,
                }
            )

            if len(results) >= count:
                break

    return results


def fetch_all_portfolio_filings(tickers: list) -> list:
    """Fetch recent filings for all portfolio tickers.

    Iterates through each ticker, fetching up to 5 recent filings of
    types 8-K, 10-K, and 10-Q. Includes a 0.2-second delay between
    requests to respect SEC rate limits.

    Args:
        tickers: List of ticker symbol strings.

    Returns:
        Combined list of filing dictionaries across all tickers, sorted
        by filing_date descending. Never raises — individual ticker
        failures are logged and skipped.
    """
    all_filings = []

    for i, ticker in enumerate(tickers):
        try:
            filings = fetch_recent_filings(ticker, ["8-K", "10-K", "10-Q"], 5)
            all_filings.extend(filings)
            print(f"  [{ticker}] Found {len(filings)} filings")
        except Exception as e:
            print(f"  [{ticker}] Error fetching filings: {e}")

        # Respect SEC rate limits — pause between requests
        if i < len(tickers) - 1:
            time.sleep(0.2)

    # Sort by filing date descending
    all_filings.sort(key=lambda x: x.get("filing_date", ""), reverse=True)
    return all_filings
