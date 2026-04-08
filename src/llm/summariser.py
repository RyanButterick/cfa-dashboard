"""Generates AI-powered event summaries using the Anthropic Claude API.

Provides functions for general event summaries, proactive (forward-looking)
summaries, reactive (what-just-happened) summaries, and a daily portfolio
briefing. All LLM calls are wrapped in try/except so the dashboard never
crashes if the API is unavailable or the key is missing.
"""

import json
import os

import anthropic

from src.llm.prompts import (
    SUMMARISATION_PROMPT,
    PROACTIVE_PROMPT,
    REACTIVE_PROMPT,
    DAILY_BRIEFING_PROMPT,
    BATCH_SCORING_PROMPT,
)

# Default model — can be overridden via Streamlit session_state
_DEFAULT_MODEL = "claude-haiku-4-5-20251001"


def _get_model() -> str:
    """Return the currently selected LLM model.

    Checks Streamlit session_state for a user override (set by the
    model selector in the Events tab). Falls back to default Haiku.
    """
    try:
        import streamlit as st
        return st.session_state.get("llm_model", _DEFAULT_MODEL)
    except Exception:
        return _DEFAULT_MODEL


def _get_client() -> anthropic.Anthropic | None:
    """Create an Anthropic client if the API key is available.

    Reads ANTHROPIC_API_KEY from environment variables (loaded via
    python-dotenv from the .env file).

    Returns:
        An Anthropic client instance, or None if the key is not configured.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    return anthropic.Anthropic(api_key=api_key)


def _build_user_message(event: dict, affected_stocks: list) -> str:
    """Build a user message from an event and affected stocks.

    Formats the event title, description, and a list of affected portfolio
    stocks with their sector and exposure tags into a structured message
    for the LLM.

    Args:
        event: Event dictionary with 'title'/'event_name' and 'description'.
        affected_stocks: List of portfolio stock dictionaries from config.

    Returns:
        Formatted string for the LLM user message.
    """
    title = event.get("title") or event.get("event_name", "N/A")
    description = event.get("description", "N/A")

    stock_descriptions = []
    for stock in affected_stocks:
        stock_descriptions.append(
            f"{stock['company_name']} ({stock['ticker']}) — {stock['sector']}, "
            f"exposure tags: {', '.join(stock.get('exposure_tags', []))}"
        )
    stocks_text = "\n".join(stock_descriptions)

    return (
        f"Event title: {title}\n"
        f"Event description: {description}\n\n"
        f"Affected portfolio stocks:\n{stocks_text}"
    )


def summarise_event(event: dict, affected_stocks: list) -> str:
    """Generate a concise AI summary of an event and its impact on holdings.

    Uses the general SUMMARISATION_PROMPT. Falls back to a plain description
    if the API key is missing or the API call fails.

    Args:
        event: Event dictionary with at least 'title' and 'description' keys.
        affected_stocks: List of portfolio stock dictionaries affected by
            the event.

    Returns:
        A 2-3 sentence summary, or a fallback message on failure.
    """
    client = _get_client()
    if client is None:
        return event.get("description", "API key not configured — summary unavailable.")

    user_message = _build_user_message(event, affected_stocks)

    try:
        response = client.messages.create(
            model=_get_model(),
            max_tokens=300,
            system=SUMMARISATION_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
        return response.content[0].text
    except Exception as e:
        print(f"Warning: LLM summarise_event failed: {e}")
        return event.get("description", "Summary unavailable — API error.")


def summarise_proactive_event(event: dict, affected_stocks: list) -> str:
    """Generate an AI summary for a proactive (upcoming) event.

    Uses the PROACTIVE_PROMPT which emphasises forward-looking analysis
    and actionable metrics to watch. Falls back gracefully on error.

    Args:
        event: Event dictionary.
        affected_stocks: List of portfolio stock dictionaries.

    Returns:
        A 2-3 sentence forward-looking summary, or fallback on failure.
    """
    client = _get_client()
    if client is None:
        return event.get("description", "API key not configured — summary unavailable.")

    user_message = _build_user_message(event, affected_stocks)

    try:
        response = client.messages.create(
            model=_get_model(),
            max_tokens=300,
            system=PROACTIVE_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
        return response.content[0].text
    except Exception as e:
        print(f"Warning: LLM summarise_proactive_event failed: {e}")
        return event.get("description", "Summary unavailable — API error.")


def summarise_reactive_event(event: dict, affected_stocks: list) -> str:
    """Generate an AI summary for a reactive (recent/breaking) event.

    Uses the REACTIVE_PROMPT which emphasises factual reporting and
    immediate implications. Falls back gracefully on error.

    Args:
        event: Event dictionary.
        affected_stocks: List of portfolio stock dictionaries.

    Returns:
        A 2-3 sentence factual summary with implications, or fallback.
    """
    client = _get_client()
    if client is None:
        return event.get("description", "API key not configured — summary unavailable.")

    user_message = _build_user_message(event, affected_stocks)

    try:
        response = client.messages.create(
            model=_get_model(),
            max_tokens=300,
            system=REACTIVE_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
        return response.content[0].text
    except Exception as e:
        print(f"Warning: LLM summarise_reactive_event failed: {e}")
        return event.get("description", "Summary unavailable — API error.")


def _score_one_batch(
    client,
    batch: list,
    index_offset: int,
    portfolio_text: str,
    portfolio_tickers: list,
) -> dict:
    """Score a single batch of headlines and return index→score mapping.

    Args:
        client: Anthropic client instance.
        batch: List of event dicts (max ~25).
        index_offset: Global index of the first item in this batch.
        portfolio_text: Pre-formatted portfolio description string.
        portfolio_tickers: List of valid ticker strings.

    Returns:
        Dict mapping global index → {score, tickers, reason}.
    """
    headline_lines = []
    for i, h in enumerate(batch):
        title = h.get("title", "No title")
        # Sanitise: remove newlines and limit length to avoid bloated prompts
        title = title.replace("\n", " ").strip()[:200]
        mention_count = h.get("mention_count", 1)
        centrality = h.get("entity_centrality", "mentioned")
        centrality_tag = "ABOUT" if centrality == "primary" else "MENTIONS"
        # Include metadata so the AI can factor in coverage and centrality
        headline_lines.append(
            f"{i}. [{mention_count}x coverage | {centrality_tag}] {title}"
        )
    headlines_text = "\n".join(headline_lines)

    user_message = (
        f"Portfolio holdings:\n{portfolio_text}\n\n"
        f"Headlines to score:\n{headlines_text}"
    )

    try:
        response = client.messages.create(
            model=_get_model(),
            max_tokens=4000,
            system=BATCH_SCORING_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
        raw_text = response.content[0].text.strip()

        # Strip markdown code fences if present
        if raw_text.startswith("```"):
            raw_text = raw_text.split("\n", 1)[-1]
        if raw_text.endswith("```"):
            raw_text = raw_text[:-3].strip()

        scores = json.loads(raw_text)

        result = {}
        for item in scores:
            local_idx = item.get("index")
            if local_idx is not None and 0 <= local_idx < len(batch):
                global_idx = index_offset + local_idx
                result[global_idx] = item
        return result

    except json.JSONDecodeError as e:
        print(f"  Warning: JSON parse error in batch (offset {index_offset}): {e}")
        return {}
    except Exception as e:
        print(f"  Warning: Batch scoring failed (offset {index_offset}): {e}")
        return {}


def batch_score_headlines(
    headlines: list, portfolio: list, min_score: int = 5
) -> list:
    """Score news headlines for portfolio relevance using AI batch calls.

    Splits headlines into chunks of 25 and sends each chunk to Claude
    in a separate API call. This avoids response truncation that causes
    JSON parse errors with large batches. Each headline is scored 1-10.

    Args:
        headlines: List of event dictionaries, each with at least a 'title' key.
        portfolio: List of portfolio stock dictionaries from config.PORTFOLIO.
        min_score: Minimum AI score (1-10) to keep an event. Default 5.

    Returns:
        Subset of headlines that scored >= min_score, each enriched with:
          - ai_score: integer 1-10
          - ai_tickers: list of affected ticker strings
          - ai_reason: one-sentence explanation
        Sorted by ai_score descending. Returns empty list if API fails
        completely (does NOT pass unscored items through).
    """
    if not headlines:
        return []

    client = _get_client()
    if client is None:
        print("Warning: No API key — skipping AI batch scoring")
        # Without AI scoring, fall back to only items the keyword
        # classifier already tagged as Direct or Sector
        return [
            h for h in headlines
            if h.get("relevance_tier") in ("Direct", "Sector")
        ]

    portfolio_tickers = [s["ticker"] for s in portfolio]
    portfolio_text = "\n".join(
        f"  {s['ticker']} — {s['company_name']} ({s['sector']}, "
        f"tags: {', '.join(s.get('exposure_tags', []))})"
        for s in portfolio
    )

    # Process in chunks of 25 to avoid response truncation
    CHUNK_SIZE = 25
    all_scores = {}

    for start in range(0, len(headlines), CHUNK_SIZE):
        batch = headlines[start : start + CHUNK_SIZE]
        print(
            f"  Scoring batch {start // CHUNK_SIZE + 1} "
            f"({len(batch)} headlines, offset {start})..."
        )
        batch_scores = _score_one_batch(
            client, batch, start, portfolio_text, portfolio_tickers
        )
        all_scores.update(batch_scores)

    # Enrich and filter headlines using collected scores
    scored = []
    for i, h in enumerate(headlines):
        if i in all_scores:
            s = all_scores[i]
            ai_score = int(s.get("score", 0))
            if ai_score >= min_score:
                h["ai_score"] = ai_score
                raw_tickers = s.get("tickers", [])
                h["ai_tickers"] = [
                    t for t in raw_tickers if t in portfolio_tickers
                ]
                h["ai_reason"] = s.get("reason", "")
                h["ai_sentiment"] = s.get("sentiment", "neutral")
                scored.append(h)

    # Sort by AI score descending
    scored.sort(key=lambda x: x.get("ai_score", 0), reverse=True)

    print(
        f"  AI batch scoring complete: {len(headlines)} headlines -> "
        f"{len(scored)} passed (min_score={min_score})"
    )
    return scored


def generate_daily_briefing(top_events: list) -> str:
    """Generate a daily briefing summary covering the top-ranked events.

    Compiles the most important events into a single paragraph-length
    briefing suitable for a portfolio manager's morning review.

    Args:
        top_events: List of the highest-ranked event dictionaries. Each
            should contain 'title', 'description', and optionally
            'affected_tickers'.

    Returns:
        A paragraph-length daily briefing string, or fallback on failure.
    """
    client = _get_client()
    if client is None:
        return "API key not configured — daily briefing unavailable."

    events_text = ""
    for i, event in enumerate(top_events, 1):
        tickers = event.get("affected_tickers", "N/A")
        events_text += (
            f"{i}. {event.get('title', event.get('event_name', 'N/A'))}\n"
            f"   Description: {event.get('description', 'N/A')}\n"
            f"   Affected tickers: {tickers}\n\n"
        )

    user_message = (
        f"Here are today's top portfolio-relevant events:\n\n{events_text}"
        f"Write a single concise paragraph briefing covering these developments."
    )

    try:
        response = client.messages.create(
            model=_get_model(),
            max_tokens=500,
            system=DAILY_BRIEFING_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
        return response.content[0].text
    except Exception as e:
        print(f"Warning: LLM generate_daily_briefing failed: {e}")
        return "Daily briefing unavailable — API error."
