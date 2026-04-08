"""Generates proactive and reactive event cards for the dashboard.

This module orchestrates the full event intelligence pipeline:

Proactive pipeline (scheduled events — earnings, macro releases):
  1. Load calendar events from JSON + yfinance
  2. Keyword-classify by relevance tier (Direct/Sector/Macro)
  3. Rank by composite score and take top N
  4. Summarise top N with Claude API

Reactive pipeline (news + filings — three-stage funnel):
  Stage 1 — Wide ingestion: load all cached news (up to 200 headlines)
            and EDGAR filings
  Stage 2 — Keyword pre-filter: fast classifier removes obvious noise,
            keeps Direct + Sector matches
  Stage 3 — AI batch scoring: send remaining headlines to Claude in ONE
            API call, score each 1-10 for portfolio relevance. Only
            headlines scoring >= 5 proceed to full summarisation.
  This catches indirect effects that keyword matching misses (e.g.
  'drug pricing reform' → JNJ/UNH) while keeping API costs low.
"""

import json
import os
import re
import sys
from datetime import datetime, date, timedelta, timezone
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime

from dotenv import load_dotenv

# Load API key from .env
load_dotenv()

# Allow imports from project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import config
from src.ingestion.calendar_data import (
    load_macro_calendar,
    fetch_earnings_dates,
    merge_calendar_events,
)
from src.processing.classifier import classify_all_events
from src.processing.ranker import rank_events
from src.llm.summariser import summarise_event, batch_score_headlines

# Base directory for resolving paths relative to project root
BASE_DIR = os.path.join(os.path.dirname(__file__), "..", "..")


def _resolve(path: str) -> str:
    """Resolve a relative path against the project root directory.

    Args:
        path: Relative path string (e.g., 'data/cache/proactive_cache.json').

    Returns:
        Absolute normalised path string.
    """
    return os.path.normpath(os.path.join(BASE_DIR, path))


def _get_affected_stocks(tickers: list) -> list:
    """Look up full portfolio stock dicts for a list of ticker strings.

    Used to provide the LLM summariser with company metadata (sector,
    exposure tags) for each affected holding.

    Args:
        tickers: List of ticker symbol strings.

    Returns:
        List of portfolio stock dictionaries from config.PORTFOLIO,
        excluding any tickers not found in the portfolio.
    """
    stocks = []
    for ticker in tickers:
        stock = config.get_stock_by_ticker(ticker)
        if stock is not None:
            stocks.append(stock)
    return stocks


def _semantic_dedup(scored_events: list, title_threshold: float = 0.55) -> list:
    """Remove semantically duplicate articles after AI scoring.

    Two articles are considered duplicates if:
      1. They share at least one AI-assigned ticker AND their titles are
         >= title_threshold similar (fuzzy match on normalised titles), OR
      2. Their titles are >= 0.70 similar regardless of tickers (same
         story, different framing).

    When duplicates are found, the article with the highest ai_score is
    kept, and its mention_count is incremented to reflect the extra
    coverage.

    Also extracts key numerical claims (e.g., "60%", "$500B") — if two
    articles about the same ticker contain the same figure, they are
    treated as duplicates even below the fuzzy threshold.

    Args:
        scored_events: List of AI-scored event dicts (must have ai_score,
            ai_tickers, and title keys).
        title_threshold: Fuzzy similarity threshold for same-ticker dedup.

    Returns:
        Deduplicated list, sorted by ai_score descending.
    """
    if not scored_events:
        return []

    def _normalise(title: str) -> str:
        """Strip source suffix and normalise for comparison."""
        if " - " in title:
            title = title.rsplit(" - ", 1)[0]
        return re.sub(r"[^a-z0-9 ]", "", title.lower()).strip()

    def _extract_numbers(title: str) -> set:
        """Extract percentage and dollar figures from a headline."""
        return set(re.findall(r"\d+(?:\.\d+)?%|\$\d+(?:\.\d+)?[BMKbmk]?", title))

    # Sort by ai_score descending so the best version survives
    sorted_events = sorted(
        scored_events, key=lambda x: x.get("ai_score", 0), reverse=True
    )

    accepted = []
    accepted_norms = []
    accepted_ticker_sets = []
    accepted_numbers = []

    for event in sorted_events:
        title = event.get("title", "")
        norm = _normalise(title)
        tickers = set(event.get("ai_tickers", []))
        numbers = _extract_numbers(title)
        is_dup = False

        for i, existing_norm in enumerate(accepted_norms):
            similarity = SequenceMatcher(None, norm, existing_norm).ratio()
            existing_tickers = accepted_ticker_sets[i]

            # Case 1: High title similarity (same story, different outlet)
            if similarity >= 0.70:
                is_dup = True
            # Case 2: Same tickers + moderate title similarity
            elif tickers and tickers & existing_tickers and similarity >= title_threshold:
                is_dup = True
            # Case 3: Same tickers + same key numbers (e.g., both say "60%")
            elif (tickers and tickers & existing_tickers
                  and numbers and numbers & accepted_numbers[i]):
                is_dup = True

            if is_dup:
                # Increment mention count on the winner
                accepted[i]["mention_count"] = (
                    accepted[i].get("mention_count", 1) + 1
                )
                break

        if not is_dup:
            accepted.append(event)
            accepted_norms.append(norm)
            accepted_ticker_sets.append(tickers)
            accepted_numbers.append(numbers)

    dedup_count = len(scored_events) - len(accepted)
    if dedup_count > 0:
        print(f"  Semantic dedup: removed {dedup_count} duplicates "
              f"({len(scored_events)} -> {len(accepted)})")

    return accepted


def _intra_ticker_topic_cluster(events: list, max_per_ticker: int = 2,
                                 topic_threshold: float = 0.45) -> list:
    """Limit per-ticker representation by collapsing same-topic stories.

    When a stock is a big mover (e.g. UNH +9%), the pipeline may surface
    5-7 stories for that ticker — most covering the same catalyst from
    different angles ("Why UNH Rallied", "UNH Jumps on CMS Rate Boost",
    "Medicare lifts payments"). This function groups each ticker's stories
    into topic clusters and keeps only the highest-scored story per cluster,
    up to max_per_ticker clusters per ticker.

    Algorithm:
      1. Group events by their primary AI ticker (first in ai_tickers list)
      2. For each ticker with >max_per_ticker stories, cluster by title
         similarity (threshold=0.40, looser than dedup to catch same-topic
         with different framing, e.g. "Why X rallied" vs "X jumps on Y")
      3. Keep the highest ai_score story from each cluster
      4. Take the top max_per_ticker clusters (by best score)
      5. Events with no ai_tickers or tickers with <=max_per_ticker stories
         pass through unchanged

    Args:
        events: List of AI-scored event dicts.
        max_per_ticker: Maximum stories to keep per ticker (default 2).
        topic_threshold: Title similarity threshold to consider two stories
            as covering the same topic (default 0.40).

    Returns:
        Filtered list preserving order, with per-ticker diversity enforced.
    """
    if not events:
        return []

    def _topic_normalise(title: str, strip_words: set) -> str:
        """Normalise title for topic comparison, stripping company/ticker names.

        This is critical — without stripping 'UnitedHealth' from every title,
        all UNH headlines look 40-50% similar to each other just from sharing
        the company name, even when they cover completely different topics.

        strip_words is a set of lowercase words to remove (ticker, company
        name tokens, and common stock-related filler).
        """
        if " - " in title:
            title = title.rsplit(" - ", 1)[0]
        title = re.sub(r"[^a-zA-Z0-9 ]", "", title).lower()
        words = [w for w in title.split() if w not in strip_words]
        return " ".join(words)

    # Group by primary ticker
    from collections import defaultdict
    ticker_groups = defaultdict(list)
    no_ticker = []

    for event in events:
        ai_tickers = event.get("ai_tickers", [])
        if ai_tickers:
            primary = ai_tickers[0]
            ticker_groups[primary].append(event)
        else:
            no_ticker.append(event)

    # For each ticker, cluster by topic and keep best per cluster
    kept_events = set()  # track by id() to preserve original objects
    trimmed_total = 0

    for ticker, group in ticker_groups.items():
        if len(group) <= max_per_ticker:
            # Under the cap — keep all
            for e in group:
                kept_events.add(id(e))
            continue

        # Sort by ai_score descending so the best story seeds each cluster
        group_sorted = sorted(
            group, key=lambda x: x.get("ai_score", 0), reverse=True
        )

        # Build a set of words to strip: ticker + company name tokens +
        # generic stock-related filler. This ensures "unitedhealth" doesn't
        # inflate similarity between all UNH headlines.
        _filler = {
            "stock", "stocks", "share", "shares", "group", "inc", "corp",
            "corporation", "company", "ltd", "nyse", "nasdaq", "the",
        }
        strip_words = _filler | {ticker.lower()}
        # Extract company name from the first event's title — take any
        # word that appears in >60% of the group's titles (likely the
        # company name tokens like "unitedhealth", "exxon", "apple")
        all_title_words = []
        for e in group:
            raw = re.sub(r"[^a-zA-Z0-9 ]", "", e.get("title", "")).lower()
            if " - " in e.get("title", ""):
                raw = re.sub(r"[^a-zA-Z0-9 ]", "",
                             e["title"].rsplit(" - ", 1)[0]).lower()
            all_title_words.append(set(raw.split()))
        # Words appearing in >60% of titles are likely company identifiers
        word_freq = {}
        for wset in all_title_words:
            for w in wset:
                word_freq[w] = word_freq.get(w, 0) + 1
        threshold_count = max(2, int(len(group) * 0.6))
        for w, count in word_freq.items():
            if count >= threshold_count and len(w) >= 4:
                strip_words.add(w)

        # Build topic clusters greedily
        clusters = []  # list of lists
        cluster_norms = []  # normalised title of the cluster seed

        for event in group_sorted:
            norm = _topic_normalise(event.get("title", ""), strip_words)
            if not norm:
                # Keep untitled events
                kept_events.add(id(event))
                continue

            matched_cluster = -1
            for ci, seed_norm in enumerate(cluster_norms):
                sim = SequenceMatcher(None, norm, seed_norm).ratio()
                if sim >= topic_threshold:
                    matched_cluster = ci
                    break

            if matched_cluster == -1:
                # New topic cluster
                clusters.append([event])
                cluster_norms.append(norm)
            else:
                clusters[matched_cluster].append(event)

        # Each cluster's representative is already the first element
        # (highest ai_score). Take top max_per_ticker clusters by
        # their representative's score.
        top_clusters = clusters[:max_per_ticker]
        for cluster in top_clusters:
            kept_events.add(id(cluster[0]))

        trimmed = len(group) - len(top_clusters)
        if trimmed > 0:
            trimmed_total += trimmed
            kept_titles = [c[0].get("title", "")[:60] for c in top_clusters]
            print(f"  Ticker cap [{ticker}]: {len(group)} stories -> "
                  f"{len(top_clusters)} topics: {kept_titles}")

    # Add all no-ticker events
    for e in no_ticker:
        kept_events.add(id(e))

    # Rebuild list preserving original order
    result = [e for e in events if id(e) in kept_events]

    if trimmed_total > 0:
        print(f"  Intra-ticker clustering: removed {trimmed_total} "
              f"redundant stories ({len(events)} -> {len(result)})")

    return result


def generate_proactive_events(
    max_events: int = 5, use_cache: bool = False
) -> list:
    """Generate proactive event cards from upcoming calendar events.

    Pipeline steps:
      1. Load macro calendar JSON + fetch earnings dates from yfinance
      2. Merge into unified timeline (exclude filings — those are past)
      3. Filter to events from today onwards
      4. Classify by relevance tier and rank by composite score
      5. Summarise top N events with Claude API
      6. Cache results for reproducibility

    Args:
        max_events: Maximum number of events to return (default 5).
        use_cache: If True, load from on-disk cache instead of generating
            fresh events. Used for reproducible demos.

    Returns:
        List of event dictionaries, each containing classification metadata
        (relevance_tier, affected_tickers) and an AI-generated summary.
    """
    cache_path = _resolve("data/cache/proactive_cache.json")

    # Return cached results if requested and available
    if use_cache and os.path.exists(cache_path):
        try:
            with open(cache_path, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"Warning: Could not load proactive cache: {e}")

    # --- Step 1: Load raw calendar data ---
    macro_path = _resolve("data/macro_calendar.json")
    macro_events = []
    earnings_events = []

    try:
        macro_events = load_macro_calendar(macro_path)
    except Exception as e:
        print(f"Warning: Could not load macro calendar: {e}")

    try:
        earnings_events = fetch_earnings_dates(config.get_all_tickers())
    except Exception as e:
        print(f"Warning: Could not fetch earnings dates: {e}")

    # --- Step 2: Merge (no filings for proactive — those are past events) ---
    all_events = merge_calendar_events(macro_events, earnings_events, [])

    # --- Step 3: Filter to today onwards ---
    today_str = date.today().isoformat()
    future_events = [
        e for e in all_events
        if str(e.get("date", ""))[:10] >= today_str
    ]

    if not future_events:
        return []

    # --- Step 4: Classify and rank ---
    classified = classify_all_events(future_events, config.PORTFOLIO)
    ranked = rank_events(classified)
    top_events = ranked[:max_events]

    # --- Step 5: Generate AI summaries for each event ---
    for event in top_events:
        # Ensure a 'title' key exists for display
        title = (
            event.get("title") or event.get("event_name", "Unknown Event")
        )
        event["title"] = title

        # Look up full stock metadata for affected tickers
        affected = _get_affected_stocks(event.get("affected_tickers", []))

        try:
            summary = summarise_event(event, affected)
            event["summary"] = summary
        except Exception as e:
            # Fallback: use the raw description if LLM fails
            print(f"Warning: LLM summary failed for '{title}': {e}")
            event["summary"] = event.get(
                "description", "Summary unavailable."
            )

        event["proactive_or_reactive"] = "proactive"
        event["timestamp"] = event.get("date", "")

    # --- Step 6: Cache results ---
    try:
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "w") as f:
            json.dump(top_events, f, indent=2, default=str)
    except Exception as e:
        print(f"Warning: Could not save proactive cache: {e}")

    return top_events


def _is_within_hours(date_str: str, hours: int) -> bool:
    """Check if a date string falls within the last N hours.

    Handles RFC 2822 dates (from Google News RSS) and ISO format dates.

    Args:
        date_str: Date string to check.
        hours: Number of hours to look back.

    Returns:
        True if the date is within the last N hours.
    """
    if not date_str:
        return False
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    try:
        dt = parsedate_to_datetime(date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt >= cutoff
    except Exception:
        pass
    try:
        dt = datetime.fromisoformat(str(date_str)[:10])
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt >= cutoff
    except Exception:
        return False


def generate_daily_reactive_events(
    max_events: int = 10, use_cache: bool = False
) -> list:
    """Generate reactive event cards from the last 48 hours only.

    Uses the same three-stage funnel as generate_reactive_events but
    filters to articles published within the last 48 hours. This provides
    a focused 'what happened today' feed alongside the broader 14-day view.

    Args:
        max_events: Maximum number of events to return (default 10).
        use_cache: If True, load from on-disk cache instead of generating.

    Returns:
        List of event dictionaries with AI-generated summaries, sorted
        by relevance. Only includes items from the last 48 hours.
    """
    cache_path = _resolve("data/cache/daily_reactive_cache.json")

    if use_cache and os.path.exists(cache_path):
        try:
            with open(cache_path, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"Warning: Could not load daily reactive cache: {e}")

    # ================================================================
    # Identify significant movers (>±3% daily change) from prices cache
    # ================================================================
    mover_tickers = set()
    prices_cache_path = _resolve("data/cache/prices_cache.json")
    if os.path.exists(prices_cache_path):
        try:
            import pandas as pd
            prices_df = pd.read_json(prices_cache_path)
            if "daily_change_pct" in prices_df.columns:
                big_movers = prices_df[
                    prices_df["daily_change_pct"].abs() >= 3.0
                ]
                mover_tickers = set(big_movers["ticker"].tolist())
                if mover_tickers:
                    print(
                        f"  Detected movers (>±3%): "
                        f"{', '.join(mover_tickers)}"
                    )
        except Exception as e:
            print(f"  Warning: Could not check movers: {e}")

    # ================================================================
    # STAGE 1 — Wide ingestion (same sources, filtered to 48 hours)
    # ================================================================
    all_items = []

    news_cache_path = _resolve("data/cache/news_cache.json")
    if os.path.exists(news_cache_path):
        try:
            with open(news_cache_path, "r") as f:
                news_items = json.load(f)
            for item in news_items:
                pub_date = item.get("published", "")
                if _is_within_hours(pub_date, 48):
                    all_items.append(
                        {
                            "title": item.get("title", ""),
                            "description": item.get("title", ""),
                            "event_type": "news",
                            "date": pub_date,
                            "source_url": item.get("url", ""),
                            "source": item.get("source", ""),
                            "mention_count": item.get("mention_count", 1),
                            "entity_centrality": item.get("entity_centrality", "mentioned"),
                        }
                    )
        except Exception as e:
            print(f"Warning: Could not load news cache: {e}")

    filings_cache_path = _resolve("data/cache/filings_cache.json")
    if os.path.exists(filings_cache_path):
        try:
            with open(filings_cache_path, "r") as f:
                filing_items = json.load(f)
            for item in filing_items:
                filing_date = item.get("filing_date", "")
                if _is_within_hours(filing_date, 48):
                    ticker = item.get("ticker", "")
                    filing_type = item.get("filing_type", "")
                    all_items.append(
                        {
                            "title": f"{ticker} {filing_type} Filing",
                            "description": item.get(
                                "description",
                                f"{filing_type} filing by {ticker}",
                            ),
                            "event_type": "filing",
                            "date": filing_date,
                            "source_url": item.get("url", ""),
                            "mention_count": 1,
                        }
                    )
        except Exception as e:
            print(f"Warning: Could not load filings cache: {e}")

    if not all_items:
        print("  Daily feed: no items from last 48 hours")
        return []

    print(f"  Daily Stage 1: {len(all_items)} items from last 48 hours")

    # ================================================================
    # STAGE 2 — Keyword pre-filter
    # ================================================================
    classified = classify_all_events(all_items, config.PORTFOLIO)

    keyword_hits = [
        e for e in classified
        if e.get("relevance_tier") in ("Direct", "Sector")
    ]
    macro_items = [
        e for e in classified
        if e.get("relevance_tier") == "Macro"
    ]

    print(
        f"  Daily Stage 2: {len(keyword_hits)} keyword hits, "
        f"{len(macro_items)} macro"
    )

    # ================================================================
    # STAGE 3 — AI batch scoring
    # ================================================================
    candidates = keyword_hits + macro_items

    ai_scored = batch_score_headlines(
        candidates, config.PORTFOLIO, min_score=5
    )

    print(f"  Daily Stage 3: {len(ai_scored)} items passed AI scoring")

    # ================================================================
    # STAGE 3b — Semantic deduplication (post-AI)
    # ================================================================
    ai_scored = _semantic_dedup(ai_scored)

    for event in ai_scored:
        if event.get("ai_tickers"):
            event["affected_tickers"] = event["ai_tickers"]
        if event.get("ai_tickers"):
            event["relevance_tier"] = "Direct"
        elif event.get("relevance_tier") == "Macro" and event.get("ai_score", 0) >= 5:
            event["relevance_tier"] = "Sector"

    # ================================================================
    # STAGE 3c — Mover score boost
    # ================================================================
    # Articles about stocks with >±3% daily moves get a relevance boost
    # because catalysts for volatile stocks are inherently more material.
    if mover_tickers:
        for event in ai_scored:
            event_tickers = set(event.get("ai_tickers", []) or
                                event.get("affected_tickers", []))
            if event_tickers & mover_tickers:
                old_score = event.get("ai_score", 0)
                event["ai_score"] = min(10, old_score + 2)
                event["is_mover_related"] = True

    # ================================================================
    # STAGE 3d — Intra-ticker topic clustering
    # ================================================================
    # When a stock has a huge move, the mover search + Yahoo Finance
    # can surface many stories for that ticker. Collapse same-topic
    # stories and keep max 2 distinct topics per ticker.
    ai_scored = _intra_ticker_topic_cluster(ai_scored, max_per_ticker=2)

    # ================================================================
    # Diversity-aware selection with guaranteed filing slots
    # ================================================================
    # Filings from portfolio companies within 48h ALWAYS appear,
    # regardless of their AI score. They are reserved first, then
    # remaining slots are filled with the best-scored news.
    # ================================================================

    # Separate guaranteed filings (from 48h window, for portfolio tickers)
    portfolio_tickers = set(config.get_all_tickers())
    guaranteed_filings = []
    remaining_scored = []

    for e in ai_scored:
        if e.get("event_type") == "filing":
            # Check if filing is from a portfolio company
            title = e.get("title", "")
            is_portfolio_filing = any(
                t in title for t in portfolio_tickers
            )
            if is_portfolio_filing:
                guaranteed_filings.append(e)
                continue
        remaining_scored.append(e)

    # Also check the original all_items for filings that didn't pass AI
    # scoring — portfolio filings should ALWAYS appear
    seen_filing_titles = {f.get("title", "") for f in guaranteed_filings}
    for item in all_items:
        if item.get("event_type") == "filing":
            title = item.get("title", "")
            if title not in seen_filing_titles:
                is_portfolio_filing = any(
                    t in title for t in portfolio_tickers
                )
                if is_portfolio_filing:
                    # Classify it so it has the required fields
                    classified_item = classify_all_events(
                        [item], config.PORTFOLIO
                    )[0]
                    classified_item["ai_score"] = 5  # Guaranteed minimum
                    classified_item["ai_sentiment"] = "neutral"
                    classified_item["ai_reason"] = "SEC filing by portfolio company"
                    guaranteed_filings.append(classified_item)
                    seen_filing_titles.add(title)

    # Reserve slots for guaranteed filings (max 3 to avoid flooding)
    max_guaranteed = min(3, len(guaranteed_filings))
    top_filings = rank_events(guaranteed_filings)[:max_guaranteed]

    # Fill remaining slots with best-scored items
    news_slots = max_events - len(top_filings)
    ranked_remaining = rank_events(remaining_scored)
    top_news = ranked_remaining[:news_slots]

    top_events = rank_events(top_filings + top_news)[:max_events]

    print(
        f"  Daily selection: {len(top_news)} news + {len(top_filings)} filings "
        f"(guaranteed) = {len(top_events)} total"
    )

    # ================================================================
    # Generate AI summaries
    # ================================================================
    for event in top_events:
        title = event.get("title", "Unknown Event")
        affected = _get_affected_stocks(event.get("affected_tickers", []))

        try:
            summary = summarise_event(event, affected)
            event["summary"] = summary
        except Exception as e:
            print(f"Warning: LLM summary failed for '{title}': {e}")
            event["summary"] = event.get(
                "description", "Summary unavailable."
            )

        event["proactive_or_reactive"] = "reactive"
        event["timestamp"] = event.get("date", "")

    # ================================================================
    # Cache results
    # ================================================================
    try:
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "w") as f:
            json.dump(top_events, f, indent=2, default=str)
    except Exception as e:
        print(f"Warning: Could not save daily reactive cache: {e}")

    return top_events


def generate_reactive_events(
    max_events: int = 5, use_cache: bool = False
) -> list:
    """Generate reactive event cards using the three-stage funnel.

    Stage 1 — Wide ingestion:
      Load all cached news headlines (up to 200 from fuzzy-deduplicated
      RSS) and EDGAR filings into a common schema.

    Stage 2 — Keyword pre-filter:
      Run the fast keyword classifier. Keep Direct and Sector matches
      (these are high-confidence). Also keep Macro items — they will
      be re-evaluated by AI in Stage 3.

    Stage 3 — AI batch scoring:
      Send all remaining headlines to Claude in a single API call.
      Each is scored 1-10. Only items scoring >= 5 survive. This
      catches indirect effects keyword matching misses.

    Finally, the top N survivors are individually summarised by Claude.

    Args:
        max_events: Maximum number of events to return (default 5).
        use_cache: If True, load from on-disk cache instead of generating
            fresh events.

    Returns:
        List of event dictionaries with AI-generated summaries, sorted
        by relevance.
    """
    cache_path = _resolve("data/cache/reactive_cache.json")

    # Return cached results if requested and available
    if use_cache and os.path.exists(cache_path):
        try:
            with open(cache_path, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"Warning: Could not load reactive cache: {e}")

    # ================================================================
    # STAGE 1 — Wide ingestion
    # ================================================================
    all_items = []

    # Load cached news headlines (already fuzzy-deduplicated by news.py)
    news_cache_path = _resolve("data/cache/news_cache.json")
    if os.path.exists(news_cache_path):
        try:
            with open(news_cache_path, "r") as f:
                news_items = json.load(f)
            for item in news_items:
                all_items.append(
                    {
                        "title": item.get("title", ""),
                        "description": item.get("title", ""),
                        "event_type": "news",
                        "date": item.get("published", ""),
                        "source_url": item.get("url", ""),
                        "source": item.get("source", ""),
                        "mention_count": item.get("mention_count", 1),
                        "entity_centrality": item.get("entity_centrality", "mentioned"),
                    }
                )
        except Exception as e:
            print(f"Warning: Could not load news cache: {e}")

    # Load cached EDGAR filings (mention_count always 1)
    filings_cache_path = _resolve("data/cache/filings_cache.json")
    if os.path.exists(filings_cache_path):
        try:
            with open(filings_cache_path, "r") as f:
                filing_items = json.load(f)
            for item in filing_items:
                ticker = item.get("ticker", "")
                filing_type = item.get("filing_type", "")
                all_items.append(
                    {
                        "title": f"{ticker} {filing_type} Filing",
                        "description": item.get(
                            "description",
                            f"{filing_type} filing by {ticker}",
                        ),
                        "event_type": "filing",
                        "date": item.get("filing_date", ""),
                        "source_url": item.get("url", ""),
                        "mention_count": 1,
                    }
                )
        except Exception as e:
            print(f"Warning: Could not load filings cache: {e}")

    if not all_items:
        return []

    print(f"  Stage 1: {len(all_items)} raw items ingested")

    # ================================================================
    # STAGE 2 — Keyword pre-filter (zero cost, fast)
    # ================================================================
    classified = classify_all_events(all_items, config.PORTFOLIO)

    # Keep Direct and Sector (high-confidence keyword matches)
    keyword_hits = [
        e for e in classified
        if e.get("relevance_tier") in ("Direct", "Sector")
    ]
    # Also collect Macro items — AI might find them relevant
    macro_items = [
        e for e in classified
        if e.get("relevance_tier") == "Macro"
    ]

    print(
        f"  Stage 2: {len(keyword_hits)} keyword hits, "
        f"{len(macro_items)} macro (pending AI review)"
    )

    # ================================================================
    # STAGE 3 — AI batch scoring (one API call for all candidates)
    # ================================================================
    # Send keyword hits + macro items to AI for scoring.
    # Keyword hits usually score high; macro items get a fair chance
    # to prove relevance through indirect effects.
    candidates = keyword_hits + macro_items

    ai_scored = batch_score_headlines(
        candidates, config.PORTFOLIO, min_score=5
    )

    print(f"  Stage 3: {len(ai_scored)} items passed AI scoring")

    # ================================================================
    # STAGE 3b — Semantic deduplication (post-AI)
    # ================================================================
    ai_scored = _semantic_dedup(ai_scored)

    # If AI scoring returned enriched results, use ai_tickers for
    # affected_tickers (AI is better at identifying indirect effects).
    for event in ai_scored:
        if event.get("ai_tickers"):
            event["affected_tickers"] = event["ai_tickers"]
        # Upgrade relevance_tier based on AI tickers
        if event.get("ai_tickers"):
            event["relevance_tier"] = "Direct"
        elif event.get("relevance_tier") == "Macro" and event.get("ai_score", 0) >= 5:
            event["relevance_tier"] = "Sector"

    # NOTE: No intra-ticker clustering here — over a 14-day window a
    # single ticker can legitimately have multiple distinct catalysts
    # (earnings, FDA decision, M&A rumour, etc.). The per-ticker cap
    # is only applied in the daily pipeline where mover-driven search
    # floods a single ticker with same-catalyst coverage.

    # ================================================================
    # Diversity-aware selection: ensure a mix of news and filings
    # ================================================================
    # Split into news and filings, rank each separately, then interleave
    news_items = [e for e in ai_scored if e.get("event_type") == "news"]
    filing_items = [e for e in ai_scored if e.get("event_type") == "filing"]

    ranked_news = rank_events(news_items)
    ranked_filings = rank_events(filing_items)

    # Reserve at least 3 slots for news (if available), max 2 for filings
    max_filings = min(2, len(ranked_filings))
    max_news = max_events - max_filings

    top_news = ranked_news[:max_news]
    top_filings = ranked_filings[:max_filings]

    # If not enough news to fill slots, allow more filings
    if len(top_news) < max_news:
        extra_filings = max_news - len(top_news)
        top_filings = ranked_filings[:max_filings + extra_filings]

    top_events = rank_events(top_news + top_filings)[:max_events]

    print(
        f"  Selection: {len(top_news)} news + {len(top_filings)} filings "
        f"= {len(top_events)} total"
    )

    # ================================================================
    # Generate full AI summaries for the final top events
    # ================================================================
    for event in top_events:
        title = event.get("title", "Unknown Event")
        affected = _get_affected_stocks(event.get("affected_tickers", []))

        try:
            summary = summarise_event(event, affected)
            event["summary"] = summary
        except Exception as e:
            print(f"Warning: LLM summary failed for '{title}': {e}")
            event["summary"] = event.get(
                "description", "Summary unavailable."
            )

        event["proactive_or_reactive"] = "reactive"
        event["timestamp"] = event.get("date", "")

    # ================================================================
    # Cache results for reproducibility
    # ================================================================
    try:
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "w") as f:
            json.dump(top_events, f, indent=2, default=str)
    except Exception as e:
        print(f"Warning: Could not save reactive cache: {e}")

    return top_events


def generate_sector_events(
    max_events: int = 8, use_cache: bool = False
) -> list:
    """Generate sector-level news events for the portfolio's sectors.

    Fetches news for each portfolio sector (Technology, Healthcare, etc.)
    rather than individual tickers. This captures industry-wide developments
    like sector rotations, regulatory shifts, and macro-to-sector impacts
    that company-specific searches miss.

    Uses the same three-stage funnel as generate_reactive_events but with
    sector-level search queries and a dedicated cache.

    Args:
        max_events: Maximum number of events to return (default 8).
        use_cache: If True, load from on-disk cache.

    Returns:
        List of sector event dictionaries with AI-generated summaries.
    """
    cache_path = _resolve("data/cache/sector_cache.json")

    if use_cache and os.path.exists(cache_path):
        try:
            with open(cache_path, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"Warning: Could not load sector cache: {e}")

    # ================================================================
    # STAGE 1 — Wide ingestion from sector news cache
    # ================================================================
    all_items = []

    sector_cache_path = _resolve("data/cache/sector_news_cache.json")
    if os.path.exists(sector_cache_path):
        try:
            with open(sector_cache_path, "r") as f:
                news_items = json.load(f)
            for item in news_items:
                all_items.append(
                    {
                        "title": item.get("title", ""),
                        "description": item.get("title", ""),
                        "event_type": "news",
                        "date": item.get("published", ""),
                        "source_url": item.get("url", ""),
                        "source": item.get("source", ""),
                        "mention_count": item.get("mention_count", 1),
                        "entity_centrality": item.get("entity_centrality", "mentioned"),
                        "sector_origin": item.get("sector", ""),
                    }
                )
        except Exception as e:
            print(f"Warning: Could not load sector news cache: {e}")

    if not all_items:
        print("  Sector feed: no items in sector news cache")
        return []

    print(f"  Sector Stage 1: {len(all_items)} raw sector items ingested")

    # ================================================================
    # STAGE 2 — Keyword pre-filter
    # ================================================================
    classified = classify_all_events(all_items, config.PORTFOLIO)

    # For sector feed, keep Sector and Macro items (not just Direct)
    keyword_hits = [
        e for e in classified
        if e.get("relevance_tier") in ("Direct", "Sector")
    ]
    macro_items = [
        e for e in classified
        if e.get("relevance_tier") == "Macro"
    ]

    print(
        f"  Sector Stage 2: {len(keyword_hits)} keyword hits, "
        f"{len(macro_items)} macro"
    )

    # ================================================================
    # STAGE 3 — AI batch scoring (lower threshold for sector news)
    # ================================================================
    candidates = keyword_hits + macro_items

    ai_scored = batch_score_headlines(
        candidates, config.PORTFOLIO, min_score=4
    )

    print(f"  Sector Stage 3: {len(ai_scored)} items passed AI scoring")

    # Enrich with AI-assigned tickers
    for event in ai_scored:
        if event.get("ai_tickers"):
            event["affected_tickers"] = event["ai_tickers"]
        if event.get("ai_tickers"):
            event["relevance_tier"] = "Sector"
        elif event.get("relevance_tier") == "Macro" and event.get("ai_score", 0) >= 4:
            event["relevance_tier"] = "Sector"

    # ================================================================
    # Rank and select top events
    # ================================================================
    ranked = rank_events(ai_scored)
    top_events = ranked[:max_events]

    print(f"  Sector selection: {len(top_events)} events")

    # ================================================================
    # Generate AI summaries
    # ================================================================
    for event in top_events:
        title = event.get("title", "Unknown Event")
        affected = _get_affected_stocks(event.get("affected_tickers", []))

        try:
            summary = summarise_event(event, affected)
            event["summary"] = summary
        except Exception as e:
            print(f"Warning: LLM summary failed for '{title}': {e}")
            event["summary"] = event.get(
                "description", "Summary unavailable."
            )

        event["proactive_or_reactive"] = "reactive"
        event["timestamp"] = event.get("date", "")

    # ================================================================
    # Cache results
    # ================================================================
    try:
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "w") as f:
            json.dump(top_events, f, indent=2, default=str)
    except Exception as e:
        print(f"Warning: Could not save sector cache: {e}")

    return top_events
