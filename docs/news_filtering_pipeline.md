# News Filtering Pipeline — How Events Reach the Dashboard

This document describes the complete end-to-end process by which news articles are ingested, filtered, scored, ranked, and displayed on the Events tab of the Portfolio Catalyst Dashboard.

The pipeline is inspired by institutional news processing systems (RavenPack, Bloomberg CN) and uses a three-stage funnel design: cast a wide net, then progressively narrow using increasingly expensive filters.


## Stage 0 — News Ingestion (`src/ingestion/news.py`)

**What happens:** Raw news headlines are fetched from two sources.

1. **Google News RSS** (primary, no API key required)
   - For each of the 10 portfolio tickers, a Google News RSS query is sent: `{TICKER} stock`
   - Up to 20 articles per ticker are collected (max ~200 raw articles)
   - A 0.5-second delay between tickers prevents rate limiting

2. **NewsAPI** (optional supplement, requires free API key)
   - If a NewsAPI key is configured, up to 10 additional articles per ticker are fetched
   - Results are appended to the RSS articles

**Entity centrality** is computed during ingestion: if the ticker symbol appears in the first half of the headline, the article is tagged `primary` (the article is *about* that company). Otherwise it is tagged `mentioned` (the company is referenced in passing). This distinction feeds into later scoring — a headline that is primarily about AAPL matters more to AAPL than one that mentions Apple in passing.

**Output:** ~150-200 raw articles with title, source name, URL, published date, associated ticker, and entity centrality tag.


## Stage 0.5 — Fuzzy Deduplication & Mention Counting (`src/ingestion/news.py`)

**What happens:** The same story is often reported by Reuters, CNBC, Bloomberg, Yahoo Finance, etc. Rather than showing 8 copies, the deduplicator collapses them into one.

1. Articles are sorted by published date (most recent first)
2. Each title is normalised: the trailing source suffix is stripped (e.g. `" - Reuters"`), then lowercased and stripped of non-alphanumeric characters
3. Each normalised title is compared against all previously accepted titles using `SequenceMatcher` (Python's built-in fuzzy string matching)
4. If the similarity ratio exceeds **70%**, the article is treated as a duplicate of an existing cluster
5. Instead of discarding the duplicate, its `mention_count` on the original cluster is incremented — so a story reported by 8 outlets gets `mention_count: 8`
6. If any duplicate in a cluster has `entity_centrality: "primary"`, the whole cluster is upgraded to `primary`

**Date filtering:** Only articles from the last **14 days** are kept. Older articles are discarded.

**Output:** ~40-80 unique articles, each with a `mention_count` (how many outlets reported it) and `entity_centrality` tag. Saved to `data/cache/news_cache.json`.


## Stage 1 — Wide Ingestion into Common Schema (`src/processing/event_generator.py`)

**What happens:** The cached news articles and SEC EDGAR filings are loaded and converted into a common event format.

For the **14-Day Intelligence** feed (`generate_reactive_events`):
- All news from `news_cache.json` (last 14 days)
- All filings from `filings_cache.json`
- Each item gets: title, description, event_type (news/filing), date, source URL, source name, mention_count, entity_centrality

For the **Today's Headlines** feed (`generate_daily_reactive_events`):
- Same sources, but filtered to articles published within the **last 48 hours** only
- Maximum 1 filing allowed (to prevent filing dominance)

**Output:** All items in a unified format, ready for classification.


## Stage 2 — Keyword Pre-Filter (`src/processing/classifier.py`)

**What happens:** A fast, zero-cost keyword classifier scans each headline and assigns a relevance tier.

- **Direct**: The headline explicitly mentions a portfolio ticker or company name (e.g. "AAPL", "Apple", "JPMorgan")
- **Sector**: The headline mentions a sector keyword matching one of the portfolio's sectors (e.g. "technology", "healthcare", "energy")
- **Macro**: The headline contains broad economic keywords (e.g. "Fed", "interest rate", "GDP", "inflation")
- Items that match none of these tiers are **discarded** — they are irrelevant noise

Both `Direct + Sector` hits and `Macro` items are kept. Macro items proceed to AI scoring where they get a fair chance to prove relevance through indirect effects (e.g. "oil price shock" → XOM, CAT, JNJ).

**Output:** Typically 30-60 items survive keyword filtering (from 40-80 input).


## Stage 2.5 — Novelty Filter (`src/processing/event_generator.py`)

**What happens:** Inspired by RavenPack's "Event Similarity Days" metric, this filter prevents the feed from being dominated by multiple articles about the same type of event for the same company.

1. Each headline is classified into an **event type** using regex patterns: earnings, legal, m&a, regulatory, management, product, macro, or other
2. For each combination of (affected_ticker, event_type), only the best article is kept — the one with the highest mention_count
3. **Exceptions** that always pass through:
   - Articles with `mention_count > 3` (major stories covered by many outlets)
   - Macro events with no specific ticker
   - The first article seen for each (ticker, event_type) pair

**Example:** If there are 4 articles about JPM earnings, only the one reported by the most outlets survives. But a JPM earnings article AND a JPM legal article both survive because they are different event types.

**Output:** Typically reduces candidates by 10-20%.


## Stage 3 — AI Batch Scoring (`src/llm/summariser.py`)

**What happens:** The remaining candidates are sent to Claude (Haiku model) in batches of 25 for relevance scoring. This is the most expensive step but catches what keywords miss.

**What the AI sees for each headline:**
- The headline text
- Coverage intensity tag: `[3x coverage]` meaning 3 outlets reported it
- Entity centrality tag: `[ABOUT]` or `[MENTIONS]`
- The full portfolio list with tickers, sectors, and exposure tags

**What the AI returns for each headline:**
- **Score (1-10):** How likely the headline is to move share prices of portfolio holdings
- **Tickers:** Which specific portfolio holdings are affected
- **Sentiment:** `positive`, `negative`, or `neutral` — the likely price direction
- **Reason:** One-sentence explanation

**Scoring guidelines built into the prompt:**
- **10:** Directly names a holding + material event (earnings surprise, FDA ruling, M&A, lawsuit, CEO change)
- **7-9:** Strongly affects a holding's sector, supply chain, or revenue driver even if no holding is named (e.g. NVIDIA earnings → AAPL/MSFT/AMZN; oil price shock → XOM/CAT; Fed rate decision → JPM/NEE)
- **4-6:** Moderate indirect effect via macro conditions
- **1-3:** Generic market noise, opinion pieces, irrelevant industries
- **1-2 (explicitly suppressed):** Routine corporate actions — director stock compensation, Form 4 filings, board appointments, dividend reiterations, standard periodic filings without surprises

Only headlines scoring **>= 5** survive.

**Output:** Typically 8-20 items pass AI scoring, each enriched with ai_score, ai_tickers, ai_sentiment, and ai_reason.


## Stage 4 — Composite Ranking (`src/processing/ranker.py`)

**What happens:** Surviving events are ranked by a 6-factor composite score (0-100 scale) inspired by RavenPack's multi-factor approach.

| Factor | Weight | Details |
|--------|--------|---------|
| **Relevance tier** | 35% | Direct=35, Sector=21, Macro=10 |
| **Recency** | 15% | 0-3 days=15, 4-7 days=11, 8-14 days=7, 15+=3 |
| **Source type** | 5% | Earnings=5, Filing=4, Macro=3, Other=2 |
| **Coverage intensity** | 20% | Logarithmic scale based on mention_count (0 for single source, up to 20 for widely reported) |
| **Source credibility** | 10% | 4-tier system: Reuters/Bloomberg/WSJ=10, CNBC/MarketWatch=7, Yahoo/Forbes=4, Unknown=2 |
| **AI sentiment strength** | 15% | Strong positive or negative=15, Neutral=4.5, Unknown=7.5 |

The logarithmic scale for coverage means going from 1 to 3 outlets matters much more than going from 8 to 10 — this reflects diminishing returns on additional coverage.

**Diversity-aware selection:** The final selection enforces a mix of content types. For the 14-day feed, max 2 filings are allowed (remaining slots go to news). For the daily feed, max 1 filing.

**Output:** The top N events (10 for 14-day, 5 for daily), sorted by composite score.


## Stage 5 — AI Summarisation (`src/llm/summariser.py`)

**What happens:** Each top event gets an individual Claude API call to generate a 2-3 sentence analyst-quality summary.

The summary prompt requires:
- Sentence 1: State what happened factually
- Sentence 2-3: Name the specific portfolio tickers affected and explain the financial mechanism (e.g. "This pressures JPM because higher rates widen net interest margins")
- No vague statements like "this could impact markets" — always name a ticker and a specific mechanism

**Output:** Each event now has a polished summary ready for display.


## Stage 6 — Display (`app.py`)

**What happens:** Events are rendered as expandable cards in two columns.

Each card shows:
- **Title** with inline badges
- **Coverage badge** (e.g. "5x coverage") — blue, only if mention_count > 1
- **Sentiment badge** — green "Bullish", red "Bearish", or grey "Neutral"
- **Centrality badge** — purple "Primary" if the article is primarily about a portfolio holding
- **Relevance tier badge** — green "Direct", amber "Sector", or grey "Macro"
- **Affected tickers**
- **Source attribution** with time-ago display (e.g. "Reuters · 3 hours ago")
- **AI-generated summary**
- **Source link**

The two columns are:
1. **Today's Headlines** (left): Top 5 events from the last 48 hours
2. **14-Day Intelligence** (right): Top 10 events from the last 14 days


## Summary of Filter Funnel

```
~200 raw articles (Google News RSS + NewsAPI)
    ↓ Fuzzy deduplication + 14-day date filter
~40-80 unique articles
    ↓ Keyword pre-filter (Direct/Sector/Macro)
~30-60 classified items
    ↓ Novelty filter (1 per ticker+event_type)
~25-50 novel items
    ↓ AI batch scoring (score >= 5 to pass)
~8-20 AI-approved items
    ↓ Composite 6-factor ranking + diversity selection
Top 5-10 events displayed
    ↓ Individual AI summarisation
Final cards with analyst-quality summaries
```

Each stage is progressively more expensive but more accurate, so cheap filters remove obvious noise before the AI ever sees it. This keeps API costs low while maintaining high-quality output.
