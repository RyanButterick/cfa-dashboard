"""
Portfolio configuration for the CFA Dashboard.

Supports both a hardcoded default portfolio (for demos and fallback) and
a dynamic portfolio populated at runtime via Streamlit session state.

When the user enters tickers through the onboarding form, the dashboard
calls set_portfolio() which stores the portfolio in memory and makes it
available to all modules via PORTFOLIO, get_all_tickers(), etc.

The default portfolio spans 8 sectors to provide broad market coverage
and is used when no dynamic portfolio has been configured.
"""

import streamlit as st

# ---------------------------------------------------------------------------
# Default portfolio — used as fallback / demo mode
# ---------------------------------------------------------------------------
_DEFAULT_PORTFOLIO = [
    {
        "ticker": "AAPL",
        "company_name": "Apple Inc.",
        "sector": "Technology",
        "sub_sector": "Consumer Electronics",
        "geography": "US",
        "shares": 0,
        "exposure_tags": [
            "consumer electronics", "iPhone", "services revenue",
            "China supply chain", "AI", "app store", "semiconductor demand"
        ],
    },
    {
        "ticker": "MSFT",
        "company_name": "Microsoft Corp.",
        "sector": "Technology",
        "sub_sector": "Software & Cloud",
        "geography": "US",
        "shares": 0,
        "exposure_tags": [
            "cloud computing", "Azure", "enterprise software",
            "AI", "gaming", "Office 365", "cybersecurity"
        ],
    },
    {
        "ticker": "JPM",
        "company_name": "JPMorgan Chase & Co.",
        "sector": "Financials",
        "sub_sector": "Banking",
        "geography": "US",
        "shares": 0,
        "exposure_tags": [
            "interest rates", "consumer credit", "capital markets",
            "investment banking", "regulation", "Fed policy", "loan growth"
        ],
    },
    {
        "ticker": "JNJ",
        "company_name": "Johnson & Johnson",
        "sector": "Healthcare",
        "sub_sector": "Pharmaceuticals",
        "geography": "US",
        "shares": 0,
        "exposure_tags": [
            "pharmaceuticals", "medical devices", "FDA approval",
            "healthcare regulation", "patent expiry", "drug pricing"
        ],
    },
    {
        "ticker": "XOM",
        "company_name": "Exxon Mobil Corp.",
        "sector": "Energy",
        "sub_sector": "Oil & Gas",
        "geography": "US",
        "shares": 0,
        "exposure_tags": [
            "oil price", "OPEC", "energy policy", "refining margins",
            "natural gas", "carbon regulation", "production volumes"
        ],
    },
    {
        "ticker": "AMZN",
        "company_name": "Amazon.com Inc.",
        "sector": "Consumer Discretionary",
        "sub_sector": "E-commerce & Cloud",
        "geography": "US",
        "shares": 0,
        "exposure_tags": [
            "e-commerce", "AWS", "cloud computing", "consumer spending",
            "logistics", "advertising", "AI"
        ],
    },
    {
        "ticker": "PG",
        "company_name": "Procter & Gamble Co.",
        "sector": "Consumer Staples",
        "sub_sector": "Household Products",
        "geography": "US",
        "shares": 0,
        "exposure_tags": [
            "consumer staples", "pricing power", "input costs",
            "emerging markets", "brand portfolio", "inflation impact"
        ],
    },
    {
        "ticker": "UNH",
        "company_name": "UnitedHealth Group Inc.",
        "sector": "Healthcare",
        "sub_sector": "Health Insurance",
        "geography": "US",
        "shares": 0,
        "exposure_tags": [
            "health insurance", "Medicare", "Medicaid", "healthcare policy",
            "medical costs", "regulation", "pharmacy benefits"
        ],
    },
    {
        "ticker": "CAT",
        "company_name": "Caterpillar Inc.",
        "sector": "Industrials",
        "sub_sector": "Machinery",
        "geography": "US",
        "shares": 0,
        "exposure_tags": [
            "infrastructure spending", "construction", "commodity prices",
            "China demand", "industrial cycle", "government spending"
        ],
    },
    {
        "ticker": "NEE",
        "company_name": "NextEra Energy Inc.",
        "sector": "Utilities",
        "sub_sector": "Renewable Energy",
        "geography": "US",
        "shares": 0,
        "exposure_tags": [
            "renewable energy", "interest rates", "utility regulation",
            "power demand", "clean energy policy", "solar", "wind"
        ],
    },
]


# ---------------------------------------------------------------------------
# Dynamic portfolio access
# ---------------------------------------------------------------------------
def _get_portfolio() -> list:
    """Return the active portfolio — dynamic if set, else default."""
    try:
        if "portfolio" in st.session_state and st.session_state["portfolio"]:
            return st.session_state["portfolio"]
    except Exception:
        # Outside Streamlit context (e.g. refresh_caches.py) — use default
        pass
    return _DEFAULT_PORTFOLIO


def set_portfolio(stocks: list) -> None:
    """Store a dynamic portfolio in session state.

    Args:
        stocks: List of stock dicts with keys: ticker, company_name,
                sector, sub_sector, geography, shares, exposure_tags.
    """
    st.session_state["portfolio"] = stocks
    # Clear caches so data refreshes for the new portfolio
    st.cache_data.clear()


def is_portfolio_configured() -> bool:
    """Check whether the user has configured a custom portfolio."""
    try:
        return bool(
            "portfolio" in st.session_state
            and st.session_state["portfolio"]
        )
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Public API — these are used throughout the codebase
# ---------------------------------------------------------------------------
# PORTFOLIO is accessed via module-level __getattr__ for backward
# compatibility. Modules that reference `config.PORTFOLIO` will
# dynamically get the active portfolio.
def __getattr__(name):
    if name == "PORTFOLIO":
        return _get_portfolio()
    raise AttributeError(f"module 'config' has no attribute {name}")


def get_all_tickers() -> list:
    """Return a list of all ticker symbol strings from the portfolio.

    Returns:
        List of strings, e.g. ['AAPL', 'MSFT', 'JPM', ...].
    """
    return [stock["ticker"] for stock in _get_portfolio()]


def get_stock_by_ticker(ticker: str) -> dict | None:
    """Look up a portfolio stock dictionary by its ticker symbol.

    Args:
        ticker: Ticker symbol string (case-insensitive).

    Returns:
        The matching stock dictionary, or None if not found.
    """
    for stock in _get_portfolio():
        if stock["ticker"] == ticker.upper():
            return stock
    return None


def get_all_sectors() -> list:
    """Return a list of unique sector names from the portfolio.

    Preserves the order in which sectors first appear.

    Returns:
        List of unique sector name strings.
    """
    return list(dict.fromkeys(stock["sector"] for stock in _get_portfolio()))


def get_default_portfolio() -> list:
    """Return the hardcoded default portfolio (for demo mode)."""
    return _DEFAULT_PORTFOLIO
