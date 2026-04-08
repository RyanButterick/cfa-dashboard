"""Ranks classified events by a composite relevance score.

The scoring formula combines six factors:
  - Relevance tier (35%): Direct=35, Sector=21, Macro=10
  - Recency (15%): scored by how close the event date is to today
  - Source type (5%): earnings=5, filing=4, macro=3, other=2
  - Coverage intensity (20%): how many outlets reported the story
  - Source credibility (10%): Tier 1 wires > Tier 2 mainstream > unknown
  - AI sentiment strength (15%): strong positive/negative > neutral

This produces scores from 0-100, where higher scores indicate greater
portfolio relevance. The formula is inspired by RavenPack's multi-factor
scoring approach used by quantitative hedge funds.
"""

import math
import re
from datetime import datetime, date


# ---------------------------------------------------------------------------
# Scoring weights (sum to 100)
# ---------------------------------------------------------------------------
_TIER_SCORES = {"Direct": 35, "Sector": 21, "Macro": 10}

_RECENCY_THRESHOLDS = [
    (3, 15),    # 0-3 days old → 15 points
    (7, 11),    # 4-7 days old → 11 points
    (14, 7),    # 8-14 days old → 7 points
]
_RECENCY_DEFAULT = 3  # 15+ days old → 3 points

_SOURCE_TYPE_SCORES = {"earnings": 5, "filing": 4, "macro": 3}
_SOURCE_TYPE_DEFAULT = 2

_COVERAGE_MAX = 20

_SENTIMENT_MAX = 15

# ---------------------------------------------------------------------------
# Source credibility tiers (Bloomberg-inspired)
# ---------------------------------------------------------------------------
# Tier 1 (10 pts): Wire services and premium financial news — fastest,
#   most reliable, institutional-grade. Bloomberg, Reuters, WSJ, FT.
# Tier 2 (7 pts): Major mainstream business outlets — high quality,
#   slightly slower. CNBC, MarketWatch, Barron's, AP, BBC Business.
# Tier 3 (4 pts): General news and aggregators — decent quality but
#   sometimes sensationalised. Yahoo Finance, Business Insider, Forbes.
# Tier 4 (2 pts): Unknown or low-quality sources — blogs, press releases,
#   obscure outlets. Default tier for unrecognised sources.
_CREDIBILITY_MAX = 10

_SOURCE_CREDIBILITY = {
    # Tier 1: Wire services and premium financial press
    "Reuters": 10, "Bloomberg": 10, "The Wall Street Journal": 10,
    "WSJ": 10, "Financial Times": 10, "FT": 10, "Dow Jones": 10,
    "Associated Press": 9, "AP News": 9,
    # Tier 2: Major business outlets
    "CNBC": 7, "MarketWatch": 7, "Barron's": 7, "The Economist": 7,
    "BBC": 7, "BBC News": 7, "NPR": 7, "The New York Times": 7,
    "The Washington Post": 7, "CNN": 7, "CNN Business": 7,
    "Seeking Alpha": 6, "Morningstar": 7,
    # Tier 3: General/aggregator sources
    "Yahoo Finance": 4, "Yahoo! Finance": 4, "Business Insider": 4,
    "Forbes": 4, "Investopedia": 4, "The Motley Fool": 3,
    "Benzinga": 4, "Zacks": 4, "TheStreet": 4,
    "TipRanks": 4, "24/7 Wall St.": 3, "InvestorPlace": 3,
    # SEC filings (neutral — scored via source_type instead)
    "SEC": 5, "SEC EDGAR": 5,
}
_CREDIBILITY_DEFAULT = 2  # Unknown sources


def _credibility_score(source_name: str) -> float:
    """Look up source credibility score from the tier mapping.

    Performs case-insensitive partial matching so 'reuters.com' still
    matches 'Reuters' and 'The Wall Street Journal Online' matches 'WSJ'.

    Args:
        source_name: Name of the news source.

    Returns:
        Credibility score between 0 and _CREDIBILITY_MAX.
    """
    if not source_name:
        return _CREDIBILITY_DEFAULT

    # Try exact match first (case-insensitive)
    for known, score in _SOURCE_CREDIBILITY.items():
        if known.lower() == source_name.lower():
            return score

    # Try partial match (source name contains a known name)
    source_lower = source_name.lower()
    for known, score in _SOURCE_CREDIBILITY.items():
        if known.lower() in source_lower or source_lower in known.lower():
            return score

    return _CREDIBILITY_DEFAULT


def _coverage_score(mention_count: int) -> float:
    """Calculate coverage intensity score from mention count.

    Uses a logarithmic scale so the benefit of additional mentions
    diminishes — going from 1 to 3 outlets matters more than going
    from 8 to 10.

    Args:
        mention_count: Number of outlets that reported this story.

    Returns:
        Coverage score between 0 and _COVERAGE_MAX.
    """
    if mention_count <= 1:
        return 0.0
    raw = math.log2(mention_count) * (_COVERAGE_MAX / math.log2(10))
    return min(raw, _COVERAGE_MAX)


def _sentiment_score(sentiment: str) -> float:
    """Score based on sentiment direction and strength.

    Strong sentiment (positive or negative) indicates a more impactful
    story. Neutral stories are less likely to move prices.

    Args:
        sentiment: One of 'positive', 'negative', 'neutral', or empty.

    Returns:
        Sentiment score between 0 and _SENTIMENT_MAX.
    """
    sentiment_lower = (sentiment or "").lower()
    if sentiment_lower in ("positive", "negative"):
        return _SENTIMENT_MAX  # Strong sentiment = full points
    if sentiment_lower == "neutral":
        return _SENTIMENT_MAX * 0.3  # Neutral = 30% of max
    return _SENTIMENT_MAX * 0.5  # Unknown = 50% (benefit of doubt)


# ---------------------------------------------------------------------------
# Price commentary detection — penalise noise that isn't event-driven
# ---------------------------------------------------------------------------
# Patterns that indicate price commentary rather than business catalysts
_COMMENTARY_PATTERNS = [
    # Stock/share + price movement verb (no catalyst)
    r"(?:stock|shares?)\s+(?:drops?|falls?|rises?|surges?|soars?|plunges?|dips?|climbs?|tumbles?|rallies?|sinks?|jumps?)",
    # Movement verb + percentage with no catalyst keyword
    r"(?:drops?|falls?|rises?|surges?|soars?|plunges?|dips?|climbs?|tumbles?|rallies?|sinks?|jumps?)\s+\d+",
    # Best/worst performing roundups
    r"(?:best|worst)\s+(?:performing|stocks?)",
    # Price target commentary
    r"price\s+target",
    # Buy/sell ratings
    r"(?:buy|sell|hold)\s+(?:rating|recommendation)",
    # Analyst predictions/opinions
    r"analysts?\s+(?:predict|forecast|expect|see)",
    # Should you buy/sell articles
    r"(?:why|should)\s+(?:you|investors?)\s+(?:\w+\s+)?(?:buy|sell|avoid)",
    # Stock is a buy/sell opinion
    r"stock\s+(?:is|looks)\s+(?:a\s+)?(?:buy|sell|overvalued|undervalued)",
    # Top N stocks listicles
    r"(?:top|best|worst)\s+(?:\d+\s+)?stocks?\s+(?:to|this|for)",
    # Market winners/losers
    r"market\s+(?:winners?|losers?)",
    # "How to position" / strategy advice
    r"how\s+to\s+(?:position|play|trade|invest)",
    # "Buy X or Y" comparison articles
    r"buy\s+\w+\s+or\s+\w+",
    # Upgrade/downgrade + estimates/target (analyst action, not catalyst)
    r"(?:upgraded?|downgraded?)\s+(?:over|to|from|on)\s+(?:potential|upside|downside|earnings|price)",
    # Pessimistic/optimistic forecast articles
    r"(?:pessimistic|optimistic|bullish|bearish)\s+(?:forecast|outlook|view)",
    # "Issues forecast for X stock price" — analyst outlook
    r"issues?\s+(?:\w+\s+)?(?:forecast|outlook)\s+for\s+\w+",
    # "What does it mean for us/you/investors" opinion pieces
    r"what\s+(?:does|will|could)\s+it\s+mean",
    # "Here's why" explainer-opinion hybrids (distinct from catalyst reporting)
    r"here(?:'s| is)\s+(?:why|what|how)",
    # "Mean for shareholders" investor advice
    r"mean\s+for\s+(?:shareholders?|investors?|you)",
]
_COMMENTARY_REGEX = re.compile("|".join(_COMMENTARY_PATTERNS), re.IGNORECASE)

# Patterns that indicate real catalysts (override commentary penalty)
_CATALYST_PATTERNS = [
    r"\bearnings\b", r"\brevenue\b", r"\bFDA\b", r"\bM&A\b",
    r"\bacquisition\b", r"\bmerger\b", r"\blawsuit\b", r"\bregulat",
    r"\bdeliveries\b", r"\bguidance\b", r"\brecall\b", r"\bbankruptcy\b",
    r"\bCEO\b", r"\bCFO\b", r"\blayoffs?\b", r"\brestructur",
    r"\bfiling\b", r"\bSEC\b", r"\bFed\b", r"\bCPI\b", r"\btariff",
    r"\bsanction", r"\binvestigat", r"\bcontract\b", r"\bpartnership\b",
    r"\bproduction\b", r"\bsupply\s+chain\b",
    # Government/regulatory policy actions
    r"\bMedicare\b", r"\bMedicaid\b", r"\bCMS\b",
    r"\bpayments?\s+(?:lift|increase|cut|boost|raise)",
    r"\brate\s+(?:boost|hike|cut|increase|decrease)\b",
    r"\blegislat", r"\bbill\s+pass",
    # Corporate actions
    r"\bspin.?off\b", r"\bIPO\b", r"\bbuyback\b", r"\bdividend\b",
    r"\bstock\s+split\b",
]
_CATALYST_REGEX = re.compile("|".join(_CATALYST_PATTERNS), re.IGNORECASE)

_COMMENTARY_PENALTY = 8  # Points deducted for pure price commentary


def _is_price_commentary(event: dict) -> bool:
    """Detect whether an event is pure price commentary lacking catalysts.

    Args:
        event: Event dictionary with title and optionally description.

    Returns:
        True if the headline matches commentary patterns but NOT catalyst
        patterns, indicating it's noise rather than a real event.
    """
    text = f"{event.get('title', '')} {event.get('description', '')}"
    if _COMMENTARY_REGEX.search(text):
        # Check for catalysts — if present, it's a real story
        if _CATALYST_REGEX.search(text):
            return False
        return True
    return False


def score_event(event: dict) -> float:
    """Calculate a composite relevance score (0-100) for a classified event.

    The score is a weighted sum of six factors:
      1. Relevance tier (35%): How directly the event relates to holdings
      2. Recency (15%): How close the event is to today's date
      3. Source type (5%): Credibility/impact of the event source type
      4. Coverage intensity (20%): How many outlets reported the story
      5. Source credibility (10%): Quality tier of the news source
      6. AI sentiment strength (15%): Whether sentiment is strong or neutral

    Args:
        event: Classified event dictionary.

    Returns:
        A float score where higher values indicate greater relevance.
    """
    score = 0.0

    # --- Factor 1: Relevance tier (35%) ---
    tier = event.get("relevance_tier", "Macro")
    score += _TIER_SCORES.get(tier, 10)

    # --- Factor 2: Recency (15%) ---
    event_date_str = event.get("date", "")
    if event_date_str:
        try:
            if isinstance(event_date_str, (datetime, date)):
                event_date = (
                    event_date_str
                    if isinstance(event_date_str, date)
                    else event_date_str.date()
                )
            else:
                date_str = str(event_date_str)[:10]
                event_date = datetime.strptime(date_str, "%Y-%m-%d").date()

            days_diff = abs((date.today() - event_date).days)

            recency_score = _RECENCY_DEFAULT
            for threshold_days, points in _RECENCY_THRESHOLDS:
                if days_diff <= threshold_days:
                    recency_score = points
                    break
            score += recency_score

        except (ValueError, TypeError):
            score += _RECENCY_DEFAULT
    else:
        score += _RECENCY_DEFAULT

    # --- Factor 3: Source type (5%) ---
    event_type = event.get("event_type", "other")
    score += _SOURCE_TYPE_SCORES.get(event_type, _SOURCE_TYPE_DEFAULT)

    # --- Factor 4: Coverage intensity (20%) ---
    mention_count = event.get("mention_count", 1)
    score += _coverage_score(mention_count)

    # --- Factor 5: Source credibility (10%) ---
    source_name = event.get("source", "")
    score += _credibility_score(source_name)

    # --- Factor 6: AI sentiment strength (15%) ---
    sentiment = event.get("ai_sentiment", "")
    score += _sentiment_score(sentiment)

    # --- Factor 7: Price commentary penalty ---
    # Reduce score for articles that are pure stock price commentary
    # without identifying a business catalyst.
    if _is_price_commentary(event):
        score = max(0, score - _COMMENTARY_PENALTY)
        event["is_commentary"] = True

    event["relevance_score"] = round(score, 2)
    return score


def rank_events(events: list) -> list:
    """Rank a list of classified events by their composite relevance score.

    Args:
        events: List of classified event dictionaries.

    Returns:
        The same list sorted by relevance_score descending.
    """
    for event in events:
        score_event(event)
    return sorted(
        events, key=lambda x: x.get("relevance_score", 0), reverse=True
    )
