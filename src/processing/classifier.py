"""Classifies incoming events by relevance to portfolio holdings.

Three-tier classification system:
  - Direct: Event mentions a specific holding by ticker, company name,
    or common short name. Highest priority — these are company-specific.
  - Sector: Event affects the same sector as one or more holdings,
    matched via sector names or exposure tags.
  - Macro: Broad market impact affecting most or all holdings. Default
    tier when no direct or sector match is found.

The classifier uses fast keyword matching (no LLM call required) to keep
latency low and costs at zero. It scans both the event title AND description
to avoid missing matches.

Company aliases are auto-generated from the portfolio data rather than
hardcoded, so they work with any set of stocks.
"""

import re
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
import config


def _generate_aliases(portfolio: list) -> dict:
    """Auto-generate company name aliases from portfolio data.

    Builds a mapping of lowercase name variants to ticker symbols by:
    - Using the full company name
    - Extracting the first word (if > 2 chars)
    - Splitting on common suffixes (Inc., Corp., Co., Ltd., Group)
    - Removing punctuation variants (& → and, dots, commas)

    Args:
        portfolio: List of stock dicts with 'ticker' and 'company_name'.

    Returns:
        Dict mapping lowercase alias strings to ticker symbols.
    """
    aliases = {}
    # Common suffixes to strip when generating short names
    _suffixes = [
        "inc.", "inc", "corp.", "corp", "co.", "co", "ltd.", "ltd",
        "group", "holdings", "plc", "llc", "n.v.", "s.a.", "se",
        "company", "companies", "corporation",
    ]

    for stock in portfolio:
        ticker = stock["ticker"]
        name = stock.get("company_name", "")
        if not name:
            continue

        name_lower = name.lower().strip()

        # Full name
        aliases[name_lower] = ticker

        # Strip suffixes to get core name
        core = name_lower
        for suffix in _suffixes:
            if core.endswith(f" {suffix}"):
                core = core[: -(len(suffix) + 1)].strip()
            # Also strip trailing punctuation
            core = core.rstrip(".,")

        if core and core != name_lower:
            aliases[core] = ticker

        # First word (if > 2 chars to avoid "PG", "3M" false positives)
        first_word = name.split()[0].lower().rstrip(".,")
        if len(first_word) > 2:
            aliases[first_word] = ticker

        # Handle ampersand variants: "P&G" → "p&g", "p and g"
        if "&" in name_lower:
            aliases[name_lower.replace("&", "and")] = ticker
            # Short form: "p&g", "j&j"
            parts = name_lower.split("&")
            if len(parts) == 2:
                short = (parts[0].strip().split()[-1] + "&" +
                         parts[1].strip().split()[0])
                aliases[short] = ticker

        # Handle "JPMorgan Chase" → "jpmorgan", "chase"
        words = core.split()
        for word in words:
            word = word.strip().rstrip(".,")
            if len(word) > 3:  # avoid short words
                aliases[word] = ticker

    return aliases


def _build_lookup_tables(portfolio: list) -> tuple:
    """Build lookup dictionaries for fast keyword matching.

    Creates three lookup tables from the portfolio configuration:
      1. direct_lookup — maps lowercased ticker symbols, full company names,
         and auto-generated aliases to their ticker symbol.
      2. sector_lookup — maps lowercased sector names to lists of tickers.
      3. tag_lookup — maps lowercased exposure tags to lists of tickers.

    Args:
        portfolio: List of portfolio stock dictionaries.

    Returns:
        Tuple of (direct_lookup, sector_lookup, tag_lookup) dictionaries.
    """
    direct_lookup = {}
    sector_lookup = {}
    tag_lookup = {}

    for stock in portfolio:
        ticker = stock["ticker"]

        # --- Direct lookup: ticker, full name ---
        direct_lookup[ticker.lower()] = ticker
        direct_lookup[stock["company_name"].lower()] = ticker

        # --- Sector lookup ---
        sector = stock["sector"]
        if sector.lower() not in sector_lookup:
            sector_lookup[sector.lower()] = []
        sector_lookup[sector.lower()].append(ticker)

        # --- Exposure tag lookup ---
        for tag in stock.get("exposure_tags", []):
            tag_lower = tag.lower()
            if tag_lower not in tag_lookup:
                tag_lookup[tag_lower] = []
            if ticker not in tag_lookup[tag_lower]:
                tag_lookup[tag_lower].append(ticker)

    # Add auto-generated aliases
    auto_aliases = _generate_aliases(portfolio)
    for alias, ticker in auto_aliases.items():
        direct_lookup[alias] = ticker

    return direct_lookup, sector_lookup, tag_lookup


def _is_earnings_event(event: dict) -> tuple:
    """Check if an event is a company-specific earnings report.

    Args:
        event: Event dictionary.

    Returns:
        Tuple of (is_earnings: bool, ticker: str or None).
    """
    title = (event.get("title") or event.get("event_name") or "").lower()
    description = (event.get("description") or "").lower()
    text = f"{title} {description}"

    if "earnings" not in text:
        return False, None

    portfolio = config._get_portfolio()
    for stock in portfolio:
        ticker_lower = stock["ticker"].lower()
        name_lower = stock["company_name"].lower()
        if ticker_lower in text or name_lower in text:
            return True, stock["ticker"]

    return False, None


def classify_event(event: dict, portfolio: list) -> dict:
    """Classify a single event by its relevance tier to the portfolio.

    Args:
        event: Event dictionary with title/event_name and description.
        portfolio: List of portfolio stock dictionaries.

    Returns:
        Copy of the event with relevance_tier, affected_tickers, and
        relevance_badge fields added.
    """
    # --- Special case: earnings events are always Direct ---
    is_earnings, earnings_ticker = _is_earnings_event(event)
    if is_earnings and earnings_ticker:
        result = dict(event)
        result["relevance_tier"] = "Direct"
        result["affected_tickers"] = [earnings_ticker]
        result["relevance_badge"] = "\U0001f7e2 Direct"
        return result

    direct_lookup, sector_lookup, tag_lookup = _build_lookup_tables(portfolio)

    title = event.get("title") or event.get("event_name") or ""
    description = event.get("description") or ""
    text = f"{title} {description}".lower()

    # --- Tier 1: Direct match ---
    direct_tickers = set()
    for keyword, ticker in direct_lookup.items():
        padded = f" {text} "
        if f" {keyword} " in padded:
            direct_tickers.add(ticker)

    if direct_tickers:
        result = dict(event)
        result["relevance_tier"] = "Direct"
        result["affected_tickers"] = sorted(direct_tickers)
        result["relevance_badge"] = "\U0001f7e2 Direct"
        return result

    # --- Tier 2: Sector match ---
    sector_tickers = set()

    event_sectors = event.get("affected_sectors", [])
    if isinstance(event_sectors, list):
        for sector in event_sectors:
            sector_lower = sector.lower()
            if sector_lower in sector_lookup:
                sector_tickers.update(sector_lookup[sector_lower])

    for tag, tickers_for_tag in tag_lookup.items():
        padded = f" {text} "
        if f" {tag} " in padded:
            sector_tickers.update(tickers_for_tag)

    if sector_tickers:
        result = dict(event)
        result["relevance_tier"] = "Sector"
        result["affected_tickers"] = sorted(sector_tickers)
        result["relevance_badge"] = "\U0001f7e1 Sector"
        return result

    # --- Tier 3: Macro ---
    result = dict(event)
    result["relevance_tier"] = "Macro"
    result["affected_tickers"] = [s["ticker"] for s in portfolio]
    result["relevance_badge"] = "\u26aa Macro"
    return result


def classify_all_events(events: list, portfolio: list) -> list:
    """Classify a list of events by relevance to the portfolio.

    Args:
        events: List of event dictionaries to classify.
        portfolio: List of portfolio stock dictionaries.

    Returns:
        List of classified event dictionaries.
    """
    return [classify_event(event, portfolio) for event in events]
