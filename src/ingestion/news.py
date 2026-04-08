"""Fetches recent financial news headlines for portfolio stocks.

Supports two sources:
  1. Google News RSS feeds (no API key required) — primary source
  2. NewsAPI.org (requires a free API key) — optional supplement

Results are deduplicated using fuzzy title similarity (not just exact match)
to collapse the same story reported by different outlets. Sorted by
publication date (most recent first).
"""

import re
import time
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime

import feedparser
import requests


def _normalise_title(title: str) -> str:
    """Normalise a headline for fuzzy comparison.

    Strips the trailing source name (after ' - '), lowercases, and
    removes non-alphanumeric characters so that minor wording differences
    between outlets don't prevent deduplication.

    Args:
        title: Raw article title string.

    Returns:
        Cleaned lowercase string for comparison.
    """
    # Strip source suffix (e.g. "Headline text - Reuters")
    if " - " in title:
        title = title.rsplit(" - ", 1)[0]
    return re.sub(r"[^a-z0-9 ]", "", title.lower()).strip()


def _is_duplicate(new_title: str, existing_titles: list, threshold: float = 0.70) -> bool:
    """Check if a headline is a fuzzy duplicate of any already-seen headline.

    Uses SequenceMatcher ratio — two titles scoring above the threshold
    are considered the same story from different outlets.

    Args:
        new_title: Normalised title to check.
        existing_titles: List of already-accepted normalised titles.
        threshold: Similarity ratio above which titles are duplicates.

    Returns:
        True if a sufficiently similar title already exists.
    """
    for existing in existing_titles:
        if SequenceMatcher(None, new_title, existing).ratio() >= threshold:
            return True
    return False


def _find_cluster(new_title: str, existing_titles: list, threshold: float = 0.70) -> int:
    """Find which story cluster a headline belongs to, if any.

    Args:
        new_title: Normalised title to check.
        existing_titles: List of normalised titles for accepted clusters.
        threshold: Similarity ratio above which titles are the same story.

    Returns:
        Index of the matching cluster, or -1 if no match found.
    """
    for i, existing in enumerate(existing_titles):
        if SequenceMatcher(None, new_title, existing).ratio() >= threshold:
            return i
    return -1


def fetch_yahoo_finance_rss(tickers: list, max_per_ticker: int = 20) -> list:
    """Fetch news from Yahoo Finance's company-specific RSS feeds.

    Yahoo Finance curates a news feed per stock that surfaces the most
    relevant stories — including sector-wide news that mentions the
    company. This catches headlines like "Medicare lifts payments" on the
    UNH page that Google News ticker searches miss entirely.

    Feed URL: https://finance.yahoo.com/rss/headline?s={TICKER}

    Args:
        tickers: List of ticker symbol strings.
        max_per_ticker: Maximum articles per ticker (default 20).

    Returns:
        List of article dicts with keys: title, source, url, published,
        ticker, entity_centrality.
    """
    all_articles = []

    for i, ticker in enumerate(tickers):
        url = f"https://finance.yahoo.com/rss/headline?s={ticker}"

        try:
            feed = feedparser.parse(url)
            entries = feed.entries[:max_per_ticker]

            for entry in entries:
                # Yahoo Finance RSS includes the source in the entry
                source = ""
                if hasattr(entry, "source") and hasattr(entry.source, "title"):
                    source = entry.source.title
                elif " - " in entry.get("title", ""):
                    source = entry["title"].rsplit(" - ", 1)[-1]

                raw_title = entry.get("title", "")
                # Check if ticker or company name is in the first half
                title_upper = raw_title[:len(raw_title) // 2 + 1].upper()
                centrality = (
                    "primary" if ticker.upper() in title_upper
                    else "mentioned"
                )

                all_articles.append(
                    {
                        "title": raw_title,
                        "source": source or "Yahoo Finance",
                        "url": entry.get("link", ""),
                        "published": entry.get("published", ""),
                        "ticker": ticker,
                        "entity_centrality": centrality,
                    }
                )

            print(f"  [{ticker}] Fetched {len(entries)} articles via Yahoo Finance RSS")

        except Exception as e:
            print(f"  [{ticker}] Yahoo Finance RSS failed: {e}")

        # Rate-limit
        if i < len(tickers) - 1:
            time.sleep(0.3)

    return all_articles


def fetch_news_rss(tickers: list, max_per_ticker: int = 20, portfolio: list = None) -> list:
    """Fetch news headlines for portfolio tickers via Google News RSS feeds.

    Runs TWO queries per ticker:
      1. '{ticker} stock' — catches articles using the ticker symbol
      2. '{company_name}' — catches articles using the full company name

    This ensures stories like "UnitedHealth Group settles DOJ case" are
    found even when they don't mention "UNH" in the headline. Company
    names are looked up from the portfolio list if provided.

    Args:
        tickers: List of ticker symbol strings (e.g., ['AAPL', 'JPM']).
        max_per_ticker: Maximum articles to fetch per query (default 20).
        portfolio: Optional list of portfolio stock dicts for company name
            lookups. If None, only the ticker query is used.

    Returns:
        List of news article dictionaries with keys: title, source, url,
        published, ticker. Individual ticker failures are logged and skipped.
    """
    all_articles = []

    # Build ticker→company_name map for dual-query searches
    name_map = {}
    if portfolio:
        for stock in portfolio:
            name_map[stock["ticker"]] = stock.get("company_name", "")

    for i, ticker in enumerate(tickers):
        # Build list of queries for this ticker
        queries = [f"{ticker} stock"]
        company_name = name_map.get(ticker, "")
        core_name = ""
        if company_name:
            # Use just the core company name (strip Inc., Corp., etc.)
            core_name = company_name
            for suffix in [" Inc.", " Corp.", " Co.", " Ltd.", " Group",
                           " Holdings", " Incorporated", " Corporation",
                           " plc", " PLC", " N.V.", " S.A.", " SE"]:
                core_name = core_name.replace(suffix, "")
            core_name = core_name.strip()
            if core_name and core_name.lower() != ticker.lower():
                queries.append(f"{core_name} news")

        for query in queries:
            url = (
                f"https://news.google.com/rss/search?"
                f"q={query.replace(' ', '+')}&hl=en-US&gl=US&ceid=US:en"
            )

            try:
                feed = feedparser.parse(url)
                entries = feed.entries[:max_per_ticker]

                for entry in entries:
                    # Extract the news source name from the RSS entry
                    source = ""
                    if hasattr(entry, "source") and hasattr(entry.source, "title"):
                        source = entry.source.title
                    elif " - " in entry.get("title", ""):
                        source = entry["title"].rsplit(" - ", 1)[-1]

                    # Entity centrality: is this article ABOUT the company
                    # or does it merely mention it?
                    raw_title = entry.get("title", "")
                    title_upper = raw_title[:len(raw_title) // 2 + 1].upper()
                    centrality = (
                        "primary" if (
                            ticker.upper() in title_upper
                            or (core_name and core_name.upper()[:8] in title_upper)
                        )
                        else "mentioned"
                    )

                    all_articles.append(
                        {
                            "title": raw_title,
                            "source": source,
                            "url": entry.get("link", ""),
                            "published": entry.get("published", ""),
                            "ticker": ticker,
                            "entity_centrality": centrality,
                        }
                    )

                print(f"  [{ticker}/{query[:25]}] Fetched {len(entries)} articles via RSS")

            except Exception as e:
                print(f"  [{ticker}/{query[:25]}] RSS fetch failed: {e}")

            # Rate-limit between queries
            time.sleep(0.4)

    return all_articles


def fetch_sector_news_rss(sectors: list, max_per_sector: int = 15) -> list:
    """Fetch sector-level news via Google News RSS feeds.

    Queries Google News for each sector using search terms designed to
    capture broad industry developments (not company-specific).

    Args:
        sectors: List of sector name strings (e.g., ['Technology', 'Energy']).
        max_per_sector: Maximum articles to fetch per sector (default 15).

    Returns:
        List of news article dictionaries with keys: title, source, url,
        published, sector. Individual sector failures are logged and skipped.
    """
    # Known sector queries for common GICS sectors; fallback generates
    # generic queries for any sector not in this map.
    _KNOWN_SECTOR_QUERIES = {
        "Technology": ["technology sector stocks", "tech industry news", "semiconductor industry", "AI industry"],
        "Information Technology": ["technology sector stocks", "tech industry news", "semiconductor industry"],
        "Financials": ["banking sector stocks", "financial industry news", "interest rates banks"],
        "Healthcare": ["healthcare sector stocks", "pharmaceutical industry news", "FDA drug approval"],
        "Health Care": ["healthcare sector stocks", "pharmaceutical industry news", "FDA drug approval"],
        "Energy": ["energy sector stocks", "oil prices OPEC", "renewable energy industry"],
        "Consumer Discretionary": ["consumer spending retail stocks", "e-commerce industry news"],
        "Consumer Staples": ["consumer staples stocks", "consumer goods industry"],
        "Industrials": ["industrial sector stocks", "infrastructure spending construction"],
        "Utilities": ["utilities sector stocks", "renewable energy policy power grid"],
        "Materials": ["materials sector stocks", "mining industry news", "commodity prices"],
        "Real Estate": ["real estate sector stocks", "REIT industry news", "housing market"],
        "Communication Services": ["media sector stocks", "streaming industry news", "telecom stocks"],
    }

    all_articles = []

    for i, sector in enumerate(sectors):
        queries = _KNOWN_SECTOR_QUERIES.get(
            sector,
            [f"{sector} sector stocks news", f"{sector} industry news"],
        )

        for query in queries:
            url = (
                f"https://news.google.com/rss/search?"
                f"q={query.replace(' ', '+')}&hl=en-US&gl=US&ceid=US:en"
            )

            try:
                feed = feedparser.parse(url)
                entries = feed.entries[:max_per_sector]

                for entry in entries:
                    source = ""
                    if hasattr(entry, "source") and hasattr(entry.source, "title"):
                        source = entry.source.title
                    elif " - " in entry.get("title", ""):
                        source = entry["title"].rsplit(" - ", 1)[-1]

                    all_articles.append(
                        {
                            "title": entry.get("title", ""),
                            "source": source,
                            "url": entry.get("link", ""),
                            "published": entry.get("published", ""),
                            "sector": sector,
                            "entity_centrality": "mentioned",
                        }
                    )

                print(f"  [{sector}/{query[:30]}] Fetched {len(entries)} articles")

            except Exception as e:
                print(f"  [{sector}/{query[:30]}] RSS fetch failed: {e}")

            # Rate-limit between queries
            time.sleep(0.3)

    return all_articles


def fetch_all_sector_news(sectors: list) -> list:
    """Fetch sector news from RSS, fuzzy-deduplicated and sorted.

    Similar to fetch_all_news but queries sector-level search terms
    rather than individual tickers.

    Args:
        sectors: List of sector name strings.

    Returns:
        Deduplicated list of sector news articles sorted by date descending.
    """
    articles = fetch_sector_news_rss(sectors)

    # Sort by published date descending
    articles.sort(key=lambda x: x.get("published", ""), reverse=True)

    # Fuzzy deduplication with mention counting
    accepted_normalised = []
    unique_articles = []
    for article in articles:
        title = article.get("title", "")
        if not title:
            continue
        norm = _normalise_title(title)
        if not norm:
            continue
        cluster_idx = _find_cluster(norm, accepted_normalised)
        if cluster_idx == -1:
            accepted_normalised.append(norm)
            article["mention_count"] = 1
            unique_articles.append(article)
        else:
            unique_articles[cluster_idx]["mention_count"] = (
                unique_articles[cluster_idx].get("mention_count", 1) + 1
            )

    print(
        f"  Sector news dedup: {len(articles)} raw -> "
        f"{len(unique_articles)} unique"
    )

    # Filter to last 14 days
    cutoff = datetime.now(timezone.utc) - timedelta(days=14)
    recent_articles = []
    for article in unique_articles:
        pub = article.get("published", "")
        if not pub:
            continue
        try:
            pub_dt = parsedate_to_datetime(pub)
            if pub_dt.tzinfo is None:
                pub_dt = pub_dt.replace(tzinfo=timezone.utc)
            if pub_dt >= cutoff:
                recent_articles.append(article)
        except Exception:
            recent_articles.append(article)

    print(
        f"  Sector date filter: {len(unique_articles)} -> "
        f"{len(recent_articles)} within last 14 days"
    )

    return recent_articles


def fetch_mover_news_rss(
    movers: list, max_per_query: int = 15
) -> list:
    """Fetch enhanced news for stocks with significant daily price changes.

    Uses a TIERED search strategy based on the magnitude of movement:

      Tier 1 (>±3%): Standard enhanced search — 4 queries
        1. '{ticker} stock news today'
        2. '{company_name} news'
        3. '{ticker} why stock up/down'
        4. '{company_name} {sector}'

      Tier 2 (>±4%): Adds specific catalyst queries — +3 queries
        5. 'why {company_name} stock up/down today'
        6. 'what happened to {company_name}'
        7. '{company_name} news today {change direction}'

      Tier 3 (>±5%): Adds sector-wide sweep — +3 queries
        8. '{sector} stocks jump/fall today'
        9. '{sector} industry news today'
       10. '{sector} stocks why up/down'

    This ensures that large moves (like UNH +9% on Medicare news) trigger
    the broadest possible search, including sector-level headlines that may
    not mention the specific company.

    Args:
        movers: List of dicts with keys: ticker, company_name, sector,
            daily_change_pct.
        max_per_query: Maximum articles per search query (default 15).

    Returns:
        List of news article dicts, not yet deduplicated (caller should
        merge with main news and dedup together).
    """
    all_articles = []

    for mover in movers:
        ticker = mover.get("ticker", "")
        company = mover.get("company_name", "")
        sector = mover.get("sector", "")
        change_pct = mover.get("daily_change_pct", 0)
        abs_change = abs(change_pct)
        direction = "up" if change_pct > 0 else "down"
        direction_verb = "jump" if change_pct > 0 else "fall"

        # --- Tier 1 (>3%): Standard enhanced search ---
        queries = [
            f"{ticker} stock news today",
            f"{company} news",
            f"{ticker} why stock {direction}",
        ]
        if sector:
            queries.append(f"{company} {sector}")

        # --- Tier 2 (>4%): Specific catalyst queries ---
        if abs_change >= 4.0:
            queries.extend([
                f"why {company} stock {direction} today",
                f"what happened to {company}",
                f"{company} news today {direction_verb}",
            ])

        # --- Tier 3 (>5%): Sector-wide sweep ---
        if abs_change >= 5.0 and sector:
            queries.extend([
                f"{sector} stocks {direction_verb} today",
                f"{sector} industry news today",
                f"{sector} stocks why {direction}",
            ])

        tier_label = (
            "Tier 3 (5%+)" if abs_change >= 5.0
            else "Tier 2 (4%+)" if abs_change >= 4.0
            else "Tier 1 (3%+)"
        )
        print(
            f"  [MOVER] {ticker} ({change_pct:+.1f}%) — "
            f"{tier_label}, running {len(queries)} queries"
        )

        for query in queries:
            url = (
                f"https://news.google.com/rss/search?"
                f"q={query.replace(' ', '+')}&hl=en-US&gl=US&ceid=US:en"
            )

            try:
                feed = feedparser.parse(url)
                entries = feed.entries[:max_per_query]

                for entry in entries:
                    source = ""
                    if hasattr(entry, "source") and hasattr(entry.source, "title"):
                        source = entry.source.title
                    elif " - " in entry.get("title", ""):
                        source = entry["title"].rsplit(" - ", 1)[-1]

                    raw_title = entry.get("title", "")
                    title_start = raw_title[:len(raw_title) // 2 + 1].upper()
                    centrality = (
                        "primary" if ticker.upper() in title_start
                        or company.upper().split()[0] in title_start.upper()
                        else "mentioned"
                    )

                    all_articles.append(
                        {
                            "title": raw_title,
                            "source": source,
                            "url": entry.get("link", ""),
                            "published": entry.get("published", ""),
                            "ticker": ticker,
                            "entity_centrality": centrality,
                            "is_mover_search": True,
                        }
                    )

            except Exception as e:
                print(f"  [MOVER/{ticker}] Query failed: {e}")

            time.sleep(0.3)

    print(f"  [MOVER] Fetched {len(all_articles)} articles for movers")
    return all_articles


def fetch_news_newsapi(tickers: list, api_key: str) -> list:
    """Fetch news articles for portfolio tickers using the NewsAPI.

    Queries the NewsAPI 'everything' endpoint for each ticker, sorted by
    publication date. Requires a valid API key (free tier available at
    newsapi.org).

    Args:
        tickers: List of ticker symbol strings.
        api_key: NewsAPI API key. If empty or None, returns an empty list.

    Returns:
        List of news article dictionaries with keys: title, source, url,
        published, ticker. Individual ticker failures are logged and skipped.
    """
    if not api_key:
        return []

    all_articles = []

    for i, ticker in enumerate(tickers):
        try:
            url = (
                f"https://newsapi.org/v2/everything?"
                f"q={ticker}&sortBy=publishedAt&pageSize=10&apiKey={api_key}"
            )
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            data = resp.json()

            for article in data.get("articles", []):
                all_articles.append(
                    {
                        "title": article.get("title", ""),
                        "source": article.get("source", {}).get("name", ""),
                        "url": article.get("url", ""),
                        "published": article.get("publishedAt", ""),
                        "ticker": ticker,
                    }
                )

            print(
                f"  [{ticker}] Fetched {len(data.get('articles', []))} "
                f"articles via NewsAPI"
            )

        except Exception as e:
            print(f"  [{ticker}] NewsAPI fetch failed: {e}")

        # Rate-limit: 0.2s between requests
        if i < len(tickers) - 1:
            time.sleep(0.2)

    return all_articles


def fetch_all_news(tickers: list, api_key: str = "", portfolio: list = None) -> list:
    """Fetch news from all available sources, fuzzy-deduplicated and sorted.

    Combines Google News RSS results with optional NewsAPI results.
    Removes duplicate stories using fuzzy title matching (70% similarity
    threshold) so the same event reported by Reuters, CNBC, and Bloomberg
    only appears once. Sorted by publication date descending.

    Args:
        tickers: List of ticker symbol strings.
        api_key: NewsAPI API key (can be empty to skip NewsAPI).
        portfolio: Optional portfolio list for company name queries.

    Returns:
        Combined, deduplicated list of news article dictionaries sorted
        by published date descending.
    """
    # Start with RSS — no API key needed, always available
    # Pass portfolio so the RSS fetcher can also search by company name
    articles = fetch_news_rss(tickers, max_per_ticker=20, portfolio=portfolio)

    # Add Yahoo Finance RSS — curated per-stock feeds that catch sector
    # news missed by Google News ticker searches (e.g. Medicare headlines on UNH)
    print("\n--- Yahoo Finance RSS ---")
    yahoo_articles = fetch_yahoo_finance_rss(tickers, max_per_ticker=20)
    articles.extend(yahoo_articles)
    print(f"  Combined: {len(articles)} articles (Google + Yahoo)")

    # Supplement with NewsAPI if a key is provided
    if api_key:
        newsapi_articles = fetch_news_newsapi(tickers, api_key)
        articles.extend(newsapi_articles)

    # Sort by published date descending first (keep most recent version)
    articles.sort(key=lambda x: x.get("published", ""), reverse=True)

    # Fuzzy deduplication with mention counting.
    # Instead of just discarding duplicates, count how many outlets reported
    # each story. High mention_count = major story covered by many outlets.
    accepted_normalised = []
    unique_articles = []
    for article in articles:
        title = article.get("title", "")
        if not title:
            continue
        norm = _normalise_title(title)
        if not norm:
            continue
        cluster_idx = _find_cluster(norm, accepted_normalised)
        if cluster_idx == -1:
            # New unique story — start a cluster
            accepted_normalised.append(norm)
            article["mention_count"] = 1
            unique_articles.append(article)
        else:
            # Duplicate — increment the count on the original article
            unique_articles[cluster_idx]["mention_count"] = (
                unique_articles[cluster_idx].get("mention_count", 1) + 1
            )
            # Upgrade centrality if any duplicate is "primary"
            if article.get("entity_centrality") == "primary":
                unique_articles[cluster_idx]["entity_centrality"] = "primary"

    print(
        f"  News dedup: {len(articles)} raw -> {len(unique_articles)} unique"
    )
    # Log top stories by mention count
    _top_mentions = sorted(
        unique_articles, key=lambda x: x.get("mention_count", 1), reverse=True
    )[:5]
    for _tm in _top_mentions:
        if _tm.get("mention_count", 1) > 1:
            print(
                f"    [{_tm['mention_count']}x] {_tm['title'][:80]}"
            )

    # Filter to last 14 days only
    cutoff = datetime.now(timezone.utc) - timedelta(days=14)
    recent_articles = []
    for article in unique_articles:
        pub = article.get("published", "")
        if not pub:
            continue
        try:
            # Google News RSS uses RFC 2822 dates (e.g. "Mon, 06 Apr 2026 12:00:00 GMT")
            pub_dt = parsedate_to_datetime(pub)
            if pub_dt.tzinfo is None:
                pub_dt = pub_dt.replace(tzinfo=timezone.utc)
            if pub_dt >= cutoff:
                recent_articles.append(article)
        except Exception:
            # If we can't parse the date, keep the article as a fallback
            recent_articles.append(article)

    print(
        f"  Date filter: {len(unique_articles)} unique -> "
        f"{len(recent_articles)} within last 14 days"
    )

    return recent_articles
