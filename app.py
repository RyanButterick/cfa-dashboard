"""
Portfolio-Aware Catalyst & Event Intelligence Dashboard
Main Streamlit application.

University of Nottingham — Group 28
CFA Institute AI Investment Challenge 2025-26

This is the main entry point for the dashboard. Run with:
    streamlit run app.py

The app has three tabs:
  1. Stock Holdings — live price data with trend arrows and a Plotly chart
  2. Calendar — interactive FullCalendar grid with click-to-detail popouts
  3. Events — AI-powered reactive news feed of portfolio-relevant developments
"""

import calendar as cal_module
import json
import os
from datetime import datetime, timezone

import pandas as pd
import plotly.express as px
import streamlit as st
import streamlit.components.v1 as components
from dotenv import load_dotenv

import config
from src.ingestion.prices import fetch_live_prices, load_prices_cache
from src.ingestion.calendar_data import (
    load_macro_calendar,
    fetch_earnings_dates,
    fetch_dividend_dates,
    merge_calendar_events,
)
from src.processing.event_generator import (
    generate_reactive_events,
    generate_daily_reactive_events,
    generate_sector_events,
)
from src.portfolio_setup import render_onboarding_form
from src.ingestion.news import fetch_all_news, fetch_all_sector_news
from src.ingestion.edgar import fetch_all_portfolio_filings
from src.ingestion.prices import save_prices_cache

# Load environment variables (ANTHROPIC_API_KEY) from .env file
load_dotenv()

# ---------------------------------------------------------------------------
# Page configuration
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="PACED Dashboard",
    page_icon="\U0001f4ca",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Portfolio onboarding gate — show setup form if no portfolio configured
# ---------------------------------------------------------------------------
_onboarding_container = st.empty()
if not config.is_portfolio_configured():
    with _onboarding_container.container():
        render_onboarding_form()
    st.stop()
else:
    # Explicitly clear the onboarding container so no ghost widgets persist
    _onboarding_container.empty()
    # Remove stale onboarding widget keys from session state
    _stale_keys = [k for k in st.session_state if k.startswith("setup_ticker_")
                   or k.startswith("setup_shares_") or k in ("add_row", "submit_portfolio", "demo_portfolio", "setup_rows")]
    for k in _stale_keys:
        del st.session_state[k]

# ---------------------------------------------------------------------------
# Custom CSS for event cards and visual polish
# ---------------------------------------------------------------------------
st.markdown(
    """
    <style>
    /* ── Import Open Sans ── */
    @import url('https://fonts.googleapis.com/css2?family=Open+Sans:wght@400;500;600;700;800&display=swap');

    /* ── Hide Streamlit default chrome ── */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header[data-testid="stHeader"] {background: transparent;}

    /* ── Hide default multi-page navigation in sidebar ── */
    section[data-testid="stSidebar"] [data-testid="stSidebarNav"] {
        display: none !important;
    }

    /* ── Apply Open Sans font globally (but NOT to icon elements) ── */
    html, body, [class*="css"], .stMarkdown, .stDataFrame, .stMetric,
    .stTabs, .stExpander, .stSelectbox, .stButton, h1, h2, h3, h4, h5, h6, p, span, div {
        font-family: 'Open Sans', -apple-system, BlinkMacSystemFont, sans-serif !important;
    }

    /* ── Material Symbols icon elements — hide text, show via CSS ── */
    .material-symbols-rounded,
    .material-symbols-outlined,
    span[class*="material-symbols"] {
        font-size: 0 !important;
        visibility: hidden !important;
        width: 0 !important;
        height: 0 !important;
        overflow: hidden !important;
        display: inline-block !important;
        line-height: 0 !important;
        padding: 0 !important;
        margin: 0 !important;
    }

    /* ── Dark sidebar matching ECharts Gallery ── */
    section[data-testid="stSidebar"] {
        background-color: #1B2A4A !important;
    }
    section[data-testid="stSidebar"] * {
        color: #C8D6E5 !important;
    }
    section[data-testid="stSidebar"] .stMetric label {
        color: #8899AA !important;
    }
    section[data-testid="stSidebar"] .stMetric [data-testid="stMetricValue"] {
        color: #FFFFFF !important;
    }
    section[data-testid="stSidebar"] hr {
        border-color: rgba(255,255,255,0.12) !important;
    }
    section[data-testid="stSidebar"] .stExpander {
        border-color: rgba(255,255,255,0.12) !important;
    }
    section[data-testid="stSidebar"] button {
        background-color: rgba(255,255,255,0.08) !important;
        border-color: rgba(255,255,255,0.15) !important;
        color: #FFFFFF !important;
    }
    section[data-testid="stSidebar"] button:hover {
        background-color: rgba(255,255,255,0.15) !important;
    }
    section[data-testid="stSidebar"] [data-testid="stToggle"] label span {
        color: #C8D6E5 !important;
    }

    /* ── (Terminal header moved to sidebar — class kept for reference) ── */

    /* ── Streamlit tab styling override ── */
    .stTabs [data-baseweb="tab-list"] {
        gap: 0px;
        border-bottom: 2px solid #B8E3E9;
    }
    .stTabs [data-baseweb="tab"] {
        padding: 10px 24px;
        font-weight: 700;
        color: #0B2E33 !important;
        font-size: 0.95em;
    }
    .stTabs [data-baseweb="tab"]:hover {
        color: #4F7C82 !important;
    }
    .stTabs [data-baseweb="tab"][aria-selected="true"] {
        color: #0B2E33 !important;
    }

    /* ── Event cards — blue/white palette ── */
    .card-direct {
        border-left: 5px solid #4F7C82;
        background-color: #F0F7F8;
        padding: 12px 16px;
        border-radius: 6px;
        margin-bottom: 8px;
        box-shadow: 0 1px 6px rgba(11,46,51,0.08);
        transition: transform 0.15s ease, box-shadow 0.15s ease;
    }
    .card-direct:hover { transform: translateY(-1px); box-shadow: 0 3px 12px rgba(11,46,51,0.14); }
    .card-sector {
        border-left: 5px solid #93B1B5;
        background-color: #F7FBFC;
        padding: 12px 16px;
        border-radius: 6px;
        margin-bottom: 8px;
        box-shadow: 0 1px 6px rgba(11,46,51,0.08);
        transition: transform 0.15s ease, box-shadow 0.15s ease;
    }
    .card-sector:hover { transform: translateY(-1px); box-shadow: 0 3px 12px rgba(11,46,51,0.14); }
    .card-macro {
        border-left: 5px solid #B8E3E9;
        background-color: #FAFEFF;
        padding: 12px 16px;
        border-radius: 6px;
        margin-bottom: 8px;
        box-shadow: 0 1px 6px rgba(11,46,51,0.08);
        transition: transform 0.15s ease, box-shadow 0.15s ease;
    }
    .card-macro:hover { transform: translateY(-1px); box-shadow: 0 3px 12px rgba(11,46,51,0.14); }

    /* Force dark text for all themes */
    .card-title {
        font-size: 1.0em;
        font-weight: 700;
        margin-bottom: 5px;
        color: #0B2E33 !important;
        line-height: 1.35;
    }
    .card-meta {
        font-size: 0.78em;
        color: #4F7C82 !important;
        margin-bottom: 6px;
    }
    .card-summary {
        font-size: 0.88em;
        line-height: 1.55;
        color: #1a3a3f !important;
    }

    /* ── Relevance badges — blue-teal palette ── */
    .badge-direct {
        background-color: #4F7C82;
        color: #FFFFFF;
        padding: 2px 10px;
        border-radius: 10px;
        font-size: 0.72em;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.04em;
    }
    .badge-sector {
        background-color: #93B1B5;
        color: #FFFFFF;
        padding: 2px 10px;
        border-radius: 10px;
        font-size: 0.72em;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.04em;
    }
    .badge-macro {
        background-color: #B8E3E9;
        color: #0B2E33;
        padding: 2px 10px;
        border-radius: 10px;
        font-size: 0.72em;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.04em;
    }

    /* ── Score badge (floated right inside card title) ── */
    .score-badge {
        display: inline-block;
        color: #fff;
        font-family: -apple-system, BlinkMacSystemFont, sans-serif;
        font-size: 0.68em;
        font-weight: 600;
        padding: 2px 8px;
        border-radius: 10px;
        float: right;
        margin-top: 1px;
        margin-left: 4px;
    }

    /* ── Bloomberg heatmap tile ── */
    .heatmap-tile {
        border-radius: 8px;
        padding: 14px 10px;
        text-align: center;
        min-height: 80px;
        display: flex;
        flex-direction: column;
        justify-content: center;
        cursor: default;
    }

    /* ── Daily briefing box ── */
    .briefing-box {
        background: linear-gradient(135deg, #4F7C82 0%, #0B2E33 100%);
        color: white;
        padding: 20px 24px;
        border-radius: 10px;
        margin-bottom: 18px;
        font-size: 0.88em;
        line-height: 1.5;
    }
    /* ── Custom HTML calendar grid ── */
    .cal-grid {
        width: 100%;
        border-collapse: collapse;
        font-family: 'Open Sans', sans-serif;
        font-size: 0.82em;
    }
    .cal-grid th {
        background: #0B2E33;
        color: #FFFFFF;
        padding: 8px 4px;
        text-align: center;
        font-weight: 600;
        font-size: 0.85em;
        letter-spacing: 0.03em;
    }
    .cal-grid td {
        border: 1px solid #E2E8F0;
        vertical-align: top;
        padding: 4px 5px;
        height: 90px;
        width: 14.28%;
        background: #FFFFFF;
    }
    .cal-grid td.other-month {
        background: #F7FAFC;
        color: #CBD5E0;
    }
    .cal-grid td.today {
        background: #F0F7F8;
        border: 2px solid #4F7C82;
    }
    .cal-day-num {
        font-weight: 600;
        font-size: 0.9em;
        color: #0B2E33;
        margin-bottom: 3px;
    }
    .cal-grid td.other-month .cal-day-num {
        color: #CBD5E0;
    }
    .cal-evt {
        display: block;
        font-size: 0.72em;
        line-height: 1.3;
        padding: 1px 4px;
        margin-bottom: 2px;
        border-radius: 3px;
        color: #FFFFFF;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
        max-width: 100%;
    }
    .cal-evt-macro { background: #F59E0B; color: #FFFFFF; }
    .cal-evt-earnings { background: #C084FC; color: #FFFFFF; }
    .cal-evt-filing { background: #3B82F6; color: #FFFFFF; }
    .cal-evt-dividend { background: #22C55E; color: #FFFFFF; }
    .cal-evt-ex-dividend { background: #86EFAC; color: #0B2E33; }
    .cal-nav {
        display: flex;
        justify-content: center;
        align-items: center;
        gap: 16px;
        margin-bottom: 10px;
    }
    .cal-nav-title {
        font-size: 1.15em;
        font-weight: 700;
        color: #0B2E33;
        min-width: 200px;
        text-align: center;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# JavaScript injection to fix Material Symbols icon text rendering
# ---------------------------------------------------------------------------
# Streamlit uses Material Symbols font for icons (expander arrows, sidebar
# toggle). When the font fails to load, the ligature names render as raw
# text ("arrow_right", "keyboard_double_arrow_left", etc.).
# This script runs periodically and hides any elements whose text content
# matches known icon ligature names, then optionally inserts a Unicode
# replacement character.
# ---------------------------------------------------------------------------
components.html(
    """
    <script>
    // Access the parent Streamlit document (this runs in an iframe)
    const parentDoc = window.parent.document;

    const ICON_LIGATURES = [
        'arrow_right', 'arrow_left', 'arrow_drop_down', 'arrow_drop_up',
        'keyboard_double_arrow_left', 'keyboard_double_arrow_right',
        'chevron_right', 'chevron_left', 'expand_more', 'expand_less',
        'close', 'menu', 'search', 'check', 'add', 'remove',
        'keyboard_arrow_down', 'keyboard_arrow_up',
        'keyboard_arrow_left', 'keyboard_arrow_right',
        'navigate_before', 'navigate_next',
    ];

    const REPLACEMENTS = {
        'arrow_right': '\\u25B6',
        'arrow_left': '\\u25C0',
        'arrow_drop_down': '\\u25BC',
        'arrow_drop_up': '\\u25B4',
        'keyboard_double_arrow_left': '\\u00AB',
        'keyboard_double_arrow_right': '\\u00BB',
        'chevron_right': '\\u203A',
        'chevron_left': '\\u2039',
        'expand_more': '\\u25BC',
        'expand_less': '\\u25B4',
        'close': '\\u2715',
        'navigate_before': '\\u2039',
        'navigate_next': '\\u203A',
    };

    function fixIcons() {
        // Strategy 1: Find elements with Material Symbols class
        parentDoc.querySelectorAll(
            '.material-symbols-rounded, .material-symbols-outlined, [class*="material-symbols"]'
        ).forEach(function(el) {
            if (el.getAttribute('data-icon-fixed')) return;
            var txt = el.textContent.trim().toLowerCase().replace(/\\s+/g, '_');
            if (ICON_LIGATURES.indexOf(txt) !== -1 || txt.indexOf('arrow') !== -1 || txt.indexOf('keyboard') !== -1 || txt.indexOf('chevron') !== -1) {
                var replacement = REPLACEMENTS[txt] || '';
                if (replacement) {
                    el.textContent = replacement;
                    el.style.fontFamily = "'Open Sans', sans-serif";
                    el.style.fontSize = '16px';
                    el.classList.remove('material-symbols-rounded', 'material-symbols-outlined');
                } else {
                    el.style.display = 'none';
                }
                el.setAttribute('data-icon-fixed', 'true');
            }
        });

        // Strategy 2: Walk ALL text-only spans looking for icon ligature text
        parentDoc.querySelectorAll('span, button > span, summary > span').forEach(function(el) {
            if (el.getAttribute('data-icon-fixed')) return;
            if (el.children.length > 0) return;
            var txt = el.textContent.trim();
            var txtLower = txt.toLowerCase().replace(/\\s+/g, '_');
            if (ICON_LIGATURES.indexOf(txtLower) !== -1) {
                var replacement = REPLACEMENTS[txtLower] || '';
                if (replacement) {
                    el.textContent = replacement;
                    el.style.fontFamily = "'Open Sans', sans-serif";
                    el.style.fontSize = '14px';
                    el.classList.remove('material-symbols-rounded', 'material-symbols-outlined');
                } else {
                    el.style.display = 'none';
                }
                el.setAttribute('data-icon-fixed', 'true');
            }
            // Catch partial matches like "_arrow_right" or "arrow_rightSome text"
            else if (/^_?(arrow_right|arrow_left|keyboard_double|keyboard_arrow)/i.test(txt)) {
                el.textContent = '';
                el.style.display = 'none';
                el.setAttribute('data-icon-fixed', 'true');
            }
        });
    }

    // Run immediately, then periodically for dynamically loaded content
    fixIcons();
    setInterval(fixIcons, 300);

    // Also observe DOM mutations in the parent document
    var observer = new MutationObserver(function() {
        setTimeout(fixIcons, 50);
    });
    observer.observe(parentDoc.body, { childList: true, subtree: true });
    </script>
    """,
    height=0,
    width=0,
)

# (Dashboard title moved to sidebar)



# ---------------------------------------------------------------------------
# Cached data-loading functions
# ---------------------------------------------------------------------------
@st.cache_data(ttl=300, show_spinner=False)
def _fetch_prices_live(tickers: tuple) -> pd.DataFrame:
    """Fetch live prices from yfinance (cached for 5 minutes).

    Args:
        tickers: Tuple of ticker symbol strings (tuple for hashability).

    Returns:
        DataFrame with price data for each ticker.
    """
    return fetch_live_prices(list(tickers))


@st.cache_data(ttl=300, show_spinner=False)
def _load_prices_cached(cache_path: str) -> pd.DataFrame:
    """Load cached price data from disk (cached for 5 minutes).

    Args:
        cache_path: Path to the prices_cache.json file.

    Returns:
        DataFrame with cached price data.
    """
    return load_prices_cache(cache_path)


@st.cache_data(ttl=600, show_spinner=False)
def _fetch_earnings(tickers: tuple) -> list:
    """Fetch earnings dates from yfinance (cached for 10 minutes).

    Args:
        tickers: Tuple of ticker symbol strings.

    Returns:
        List of earnings event dictionaries.
    """
    return fetch_earnings_dates(list(tickers))


@st.cache_data(ttl=600, show_spinner=False)
def _fetch_dividends(tickers: tuple) -> list:
    """Fetch dividend dates from yfinance (cached for 10 minutes).

    Args:
        tickers: Tuple of ticker symbol strings.

    Returns:
        List of dividend event dictionaries.
    """
    return fetch_dividend_dates(list(tickers))


def _refresh_raw_caches(status_callback=None) -> None:
    """Re-fetch all raw data caches (news, filings, prices, sector news).

    Called when the user clicks Refresh in the Events tab. This ensures
    the event generators work with fresh data, not stale JSON files.

    Args:
        status_callback: Optional callable to report progress (e.g.
            st.status.write or st.status.update).
    """
    cache_dir = os.path.join(os.path.dirname(__file__), "data", "cache")
    os.makedirs(cache_dir, exist_ok=True)

    tickers = config.get_all_tickers()
    portfolio = config._get_portfolio()

    def _report(msg):
        if status_callback:
            status_callback(msg)

    # 1. Refresh prices (needed for mover detection)
    _report("Fetching live prices...")
    try:
        prices_df = fetch_live_prices(tuple(tickers))
        save_prices_cache(prices_df, os.path.join(cache_dir, "prices_cache.json"))
    except Exception as e:
        print(f"Warning: Price refresh failed: {e}")

    # 2. Refresh news (with company-name queries + mover-enhanced search)
    _report("Fetching news headlines (Google + Yahoo Finance RSS)...")
    try:
        articles = fetch_all_news(tickers, portfolio=portfolio)

        # Enhanced search for movers (>±3% daily change)
        try:
            from src.ingestion.news import fetch_mover_news_rss, _normalise_title, _find_cluster
            prices_path = os.path.join(cache_dir, "prices_cache.json")
            if os.path.exists(prices_path):
                _prices = pd.read_json(prices_path)
                if "daily_change_pct" in _prices.columns:
                    big_movers = _prices[_prices["daily_change_pct"].abs() >= 3.0]
                    if not big_movers.empty:
                        movers = []
                        for _, row in big_movers.iterrows():
                            stock_info = config.get_stock_by_ticker(row["ticker"])
                            if stock_info:
                                movers.append({
                                    "ticker": row["ticker"],
                                    "company_name": stock_info.get("company_name", ""),
                                    "sector": stock_info.get("sector", ""),
                                    "daily_change_pct": float(row["daily_change_pct"]),
                                })
                        if movers:
                            tier_counts = {
                                "3%+": sum(1 for m in movers if abs(m["daily_change_pct"]) < 4),
                                "4%+": sum(1 for m in movers if 4 <= abs(m["daily_change_pct"]) < 5),
                                "5%+": sum(1 for m in movers if abs(m["daily_change_pct"]) >= 5),
                            }
                            tier_str = ", ".join(f"{v}x {k}" for k, v in tier_counts.items() if v)
                            _report(f"Tiered mover search for {len(movers)} movers ({tier_str})...")
                            mover_articles = fetch_mover_news_rss(movers)
                            accepted_norms = [_normalise_title(a["title"]) for a in articles]
                            added = 0
                            for article in mover_articles:
                                norm = _normalise_title(article.get("title", ""))
                                if not norm:
                                    continue
                                cluster_idx = _find_cluster(norm, accepted_norms)
                                if cluster_idx == -1:
                                    article["mention_count"] = 1
                                    articles.append(article)
                                    accepted_norms.append(norm)
                                    added += 1
                                else:
                                    articles[cluster_idx]["mention_count"] = (
                                        articles[cluster_idx].get("mention_count", 1) + 1
                                    )
                            _report(f"Mover search added {added} new articles")
        except Exception as e:
            print(f"Warning: Mover search failed: {e}")

        with open(os.path.join(cache_dir, "news_cache.json"), "w") as f:
            json.dump(articles, f, indent=2, default=str)
    except Exception as e:
        print(f"Warning: News refresh failed: {e}")

    # 3. Refresh SEC filings
    _report("Fetching SEC filings...")
    try:
        filings = fetch_all_portfolio_filings(tickers)
        with open(os.path.join(cache_dir, "filings_cache.json"), "w") as f:
            json.dump(filings, f, indent=2, default=str)
    except Exception as e:
        print(f"Warning: Filings refresh failed: {e}")

    # 4. Refresh sector news
    _report("Fetching sector news...")
    try:
        sectors = config.get_all_sectors()
        sector_articles = fetch_all_sector_news(sectors)
        with open(os.path.join(cache_dir, "sector_news_cache.json"), "w") as f:
            json.dump(sector_articles, f, indent=2, default=str)
    except Exception as e:
        print(f"Warning: Sector news refresh failed: {e}")

    # 5. Clear stale event caches so generators rebuild from fresh data
    for name in ("proactive_cache.json", "reactive_cache.json",
                 "daily_reactive_cache.json", "sector_cache.json"):
        path = os.path.join(cache_dir, name)
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass


@st.cache_data(ttl=600, show_spinner=False)
def _cached_reactive_events(max_events: int, use_cache: bool) -> list:
    """Generate reactive events (cached for 10 minutes).

    Args:
        max_events: Maximum number of events to generate.
        use_cache: Whether to use the on-disk event cache.

    Returns:
        List of reactive event dictionaries with summaries.
    """
    return generate_reactive_events(max_events=max_events, use_cache=use_cache)


@st.cache_data(ttl=600, show_spinner=False)
def _cached_daily_reactive_events(max_events: int, use_cache: bool) -> list:
    """Generate daily reactive events from last 48h (cached for 10 minutes).

    Args:
        max_events: Maximum number of events to generate.
        use_cache: Whether to use the on-disk event cache.

    Returns:
        List of daily reactive event dictionaries with summaries.
    """
    return generate_daily_reactive_events(max_events=max_events, use_cache=use_cache)


@st.cache_data(ttl=600, show_spinner=False)
def _cached_sector_events(max_events: int, use_cache: bool) -> list:
    """Generate sector-level events (cached for 10 minutes).

    Args:
        max_events: Maximum number of sector events to return.
        use_cache: If True, load from on-disk cache.

    Returns:
        List of sector event dictionaries with summaries.
    """
    return generate_sector_events(max_events=max_events, use_cache=use_cache)


# ---------------------------------------------------------------------------
# Helper: trend arrow column
# ---------------------------------------------------------------------------
def add_trend_column(df: pd.DataFrame) -> pd.DataFrame:
    """Add a Trend column with green/red arrows based on daily change.

    Args:
        df: DataFrame with a 'daily_change_pct' column.

    Returns:
        The same DataFrame with a new 'Trend' column containing emoji arrows.
    """
    def _arrow(val):
        if val is None or pd.isna(val):
            return "\u2014"
        if val > 0:
            return "\U0001f7e2 \u25b2"  # green circle + up arrow
        if val < 0:
            return "\U0001f534 \u25bc"  # red circle + down arrow
        return "\u2014"
    df["Trend"] = df["daily_change_pct"].apply(_arrow)
    return df


# ---------------------------------------------------------------------------
# Helper: human-readable time-ago string
# ---------------------------------------------------------------------------
def _time_ago(timestamp_str: str) -> str:
    """Convert a date/datetime string to a human-readable time-ago string.

    Args:
        timestamp_str: ISO date or RFC 2822 date string.

    Returns:
        Human-readable string like '2 hours ago', '3 days ago', etc.
    """
    if not timestamp_str:
        return ""
    try:
        # Try ISO format first (2026-03-25)
        dt = datetime.fromisoformat(str(timestamp_str)[:10])
        # Make it timezone-aware (assume UTC for date-only strings)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        delta = now - dt
        seconds = int(delta.total_seconds())

        if seconds < 0:
            return "upcoming"
        if seconds < 3600:
            mins = max(1, seconds // 60)
            return f"{mins} min{'s' if mins != 1 else ''} ago"
        if seconds < 86400:
            hours = seconds // 3600
            return f"{hours} hour{'s' if hours != 1 else ''} ago"
        days = seconds // 86400
        if days == 1:
            return "1 day ago"
        if days < 30:
            return f"{days} days ago"
        if days < 365:
            months = days // 30
            return f"{months} month{'s' if months != 1 else ''} ago"
        years = days // 365
        return f"{years} year{'s' if years != 1 else ''} ago"
    except Exception:
        return str(timestamp_str)[:10]


# ---------------------------------------------------------------------------
# HTML Calendar Grid builder
# ---------------------------------------------------------------------------
def _build_calendar_html(year: int, month: int, events: list) -> str:
    """Build a monthly HTML calendar grid with event pills.

    Args:
        year: Calendar year to display.
        month: Calendar month (1-12) to display.
        events: List of event dicts with 'date', 'event_type', 'event_name'.

    Returns:
        HTML string for the calendar grid.
    """
    import html as html_mod

    # Group events by date string
    events_by_date: dict[str, list] = {}
    for ev in events:
        d = str(ev.get("date", ""))[:10]
        if d:
            events_by_date.setdefault(d, []).append(ev)

    today_str = datetime.now().strftime("%Y-%m-%d")
    month_name = cal_module.month_name[month]

    # Build the grid using calendar module
    cal = cal_module.Calendar(firstweekday=0)  # Monday first
    weeks = cal.monthdayscalendar(year, month)

    rows_html = ""
    for week in weeks:
        row = ""
        for day in week:
            if day == 0:
                row += '<td class="other-month"><div class="cal-day-num">&nbsp;</div></td>'
                continue

            date_str = f"{year}-{month:02d}-{day:02d}"
            td_class = "today" if date_str == today_str else ""

            day_events = events_by_date.get(date_str, [])
            events_html = ""
            for ev in day_events[:3]:  # max 3 per cell
                ev_type = ev.get("event_type", "macro")
                ev_name = html_mod.escape(str(ev.get("event_name", "Event"))[:30])
                events_html += (
                    f'<span class="cal-evt cal-evt-{ev_type}" '
                    f'title="{html_mod.escape(str(ev.get("event_name", "")))}">'
                    f'{ev_name}</span>'
                )
            if len(day_events) > 3:
                events_html += (
                    f'<span style="font-size:0.7em;color:#718096;">'
                    f'+{len(day_events) - 3} more</span>'
                )

            row += (
                f'<td class="{td_class}">'
                f'<div class="cal-day-num">{day}</div>'
                f'{events_html}</td>'
            )
        rows_html += f"<tr>{row}</tr>"

    header_row = "".join(
        f"<th>{d}</th>" for d in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    )

    return (
        f'<table class="cal-grid">'
        f'<thead><tr>{header_row}</tr></thead>'
        f'<tbody>{rows_html}</tbody>'
        f'</table>'
    )


# ---------------------------------------------------------------------------
# Reusable event card renderer (CSS-styled)
# ---------------------------------------------------------------------------
def render_event_card(
    title: str,
    relevance_tier: str,
    tickers: str,
    summary: str,
    source_url: str,
    timestamp: str,
    source_name: str = "",
    mention_count: int = 1,
    sentiment: str = "",
    centrality: str = "",
) -> None:
    """Render a single event card with a coloured left border inside an expander.

    The border colour indicates the relevance tier: green for Direct,
    amber for Sector, grey for Macro. Cards are collapsed by default.
    Shows source attribution, time-ago, coverage intensity, sentiment,
    and entity centrality.

    Args:
        title: Event title text.
        relevance_tier: One of 'Direct', 'Sector', or 'Macro'.
        tickers: Comma-separated string of affected ticker symbols.
        summary: AI-generated or fallback summary text.
        source_url: URL of the event source.
        timestamp: Date string for the event.
        source_name: Name of the news source (e.g. 'Reuters', 'CNBC').
        mention_count: Number of outlets that reported this story.
        sentiment: AI-assessed sentiment direction (positive/negative/neutral).
        centrality: Entity centrality (primary/mentioned).
    """
    tier_lower = (relevance_tier or "macro").lower()
    card_class = (
        f"card-{tier_lower}"
        if tier_lower in ("direct", "sector", "macro")
        else "card-macro"
    )
    badge_class = (
        f"badge-{tier_lower}"
        if tier_lower in ("direct", "sector", "macro")
        else "badge-macro"
    )

    tickers_str = (
        tickers
        if isinstance(tickers, str)
        else ", ".join(tickers) if tickers else "N/A"
    )

    time_ago_str = _time_ago(timestamp)

    # Coverage intensity badge (only show if more than 1 source)
    coverage_html = ""
    if mention_count > 1:
        coverage_html = (
            f'<span class="score-badge" style="background:#2b6cb0;">'
            f'{mention_count}x coverage</span>'
        )

    # Sentiment badge
    sentiment_html = ""
    sentiment_lower = (sentiment or "").lower()
    if sentiment_lower == "positive":
        sentiment_html = (
            '<span class="score-badge" style="background:#38a169;">'
            '\u25b2 Bullish</span>'
        )
    elif sentiment_lower == "negative":
        sentiment_html = (
            '<span class="score-badge" style="background:#e53e3e;">'
            '\u25bc Bearish</span>'
        )
    elif sentiment_lower == "neutral":
        sentiment_html = (
            '<span class="score-badge" style="background:#718096;">'
            '\u25cf Neutral</span>'
        )

    # Entity centrality badge (only show for primary — "mentioned" is default)
    centrality_html = ""
    if (centrality or "").lower() == "primary":
        centrality_html = (
            '<span class="score-badge" style="background:#805ad5;">'
            'Primary</span>'
        )

    # Source attribution line
    source_parts = []
    if source_name:
        source_parts.append(f"<strong>{source_name}</strong>")
    if time_ago_str:
        source_parts.append(time_ago_str)
    source_line = " &bull; ".join(source_parts) if source_parts else str(timestamp)[:10]

    source_html = ""
    if source_url and str(source_url).startswith("http"):
        source_html = (
            f'<div style="margin-top:8px;">'
            f'<a href="{source_url}" target="_blank" '
            f'style="color:#4299e1; font-size:0.78em; text-decoration:none;">'
            f'View Source \u2192</a></div>'
        )

    card_html = (
        f'<div class="{card_class}">'
        f'<div class="card-title">{title}{coverage_html}{sentiment_html}{centrality_html}</div>'
        f'<div class="card-meta">'
        f'<span class="{badge_class}">{relevance_tier}</span>'
        f'&nbsp;&bull;&nbsp; <strong>Tickers:</strong> {tickers_str}'
        f'&nbsp;&bull;&nbsp; {source_line}'
        f'</div>'
        f'<div class="card-summary">{summary}</div>'
        f'{source_html}'
        f'</div>'
    )

    with st.expander(title, expanded=False):
        st.markdown(card_html, unsafe_allow_html=True)


# ===========================================================================
# Sidebar
# ===========================================================================
with st.sidebar:
    # ── PACED branding logo + subtitle ──
    st.markdown(
        '<div style="padding:12px 0 10px 0; text-align:center;">'
        # --- Inline SVG logo matching the PACED brand ---
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 260 60" '
        'style="width:200px; height:auto; margin-bottom:8px;">'
        # Circle outline
        '<circle cx="30" cy="30" r="26" fill="none" stroke="#FFFFFF" stroke-width="2"/>'
        # Bar chart inside circle (3 ascending bars)
        '<rect x="17" y="34" width="6" height="12" rx="1" fill="#FFFFFF"/>'
        '<rect x="27" y="26" width="6" height="20" rx="1" fill="#FFFFFF"/>'
        '<rect x="37" y="18" width="6" height="28" rx="1" fill="#FFFFFF"/>'
        # Curved trend line over bars
        '<path d="M16 36 Q24 28 30 26 Q36 24 44 17" fill="none" '
        'stroke="#FFFFFF" stroke-width="2" stroke-linecap="round"/>'
        # PACED text
        "<text x='68' y='38' fill='#FFFFFF' font-family='Open Sans, sans-serif' "
        "font-size='28' font-weight='700' letter-spacing='3'>PACED</text>"
        '</svg>'
        # --- Subtitle under logo ---
        '<div style="font-size:0.78em; font-weight:600; color:#C8D6E5 !important; '
        "font-family:'Open Sans',sans-serif; line-height:1.35; margin-top:2px;\">"
        'Portfolio-Aware Catalyst &amp; Event<br/>Intelligence Dashboard</div>'
        '<div style="font-size:0.65em; color:#6B8299 !important; margin-top:6px; '
        "font-family:'Open Sans',sans-serif;\">"
        'University of Nottingham &mdash; Group 28<br/>'
        'CFA Institute AI Investment Challenge 2025-26</div>'
        '</div>',
        unsafe_allow_html=True,
    )

    st.divider()

    # ── Page navigation ──
    st.markdown(
        '<p style="font-size:0.75em; text-transform:uppercase; letter-spacing:0.08em; '
        "color:#6B8299; margin-bottom:4px; font-family:'Open Sans',sans-serif;\">Navigation</p>",
        unsafe_allow_html=True,
    )
    st.page_link("app.py", label="US Portfolio", icon="\U0001f4ca")
    st.page_link("pages/international.py", label="International Portfolio", icon="\U0001f30d")
    st.page_link("pages/derivatives.py", label="Derivatives Overlay", icon="\U0001f4c9")

    st.divider()

    # ── Portfolio overview ──
    st.markdown(
        '<p style="font-size:0.75em; text-transform:uppercase; letter-spacing:0.08em; '
        "color:#6B8299; margin-bottom:4px; font-family:'Open Sans',sans-serif;\">Portfolio Overview</p>",
        unsafe_allow_html=True,
    )

    sectors = config.get_all_sectors()

    _sb_col1, _sb_col2 = st.columns(2)
    _sb_col1.metric("Holdings", len(config.get_all_tickers()))
    _sb_col2.metric("Sectors", len(sectors))

    # Mini portfolio performance block from prices cache
    _sidebar_prices_path = os.path.join(
        os.path.dirname(__file__), "data", "cache", "prices_cache.json"
    )
    if os.path.exists(_sidebar_prices_path):
        try:
            _sp_df = pd.read_json(_sidebar_prices_path)
            _valid = _sp_df.dropna(subset=["daily_change_pct"])
            if not _valid.empty:
                _avg = _valid["daily_change_pct"].mean()
                _top = _valid.loc[_valid["daily_change_pct"].idxmax()]
                _bot = _valid.loc[_valid["daily_change_pct"].idxmin()]
                _colour = "#5BE49B" if _avg >= 0 else "#FF6B6B"
                st.markdown(
                    f'<div style="background:rgba(255,255,255,0.06); border:1px solid rgba(255,255,255,0.12); '
                    f'border-radius:8px; padding:10px 14px; margin:4px 0 8px 0;">'
                    f'<span style="font-size:1.3em; font-weight:700; color:{_colour};">'
                    f'{_avg:+.2f}%</span>'
                    f'<span style="font-size:0.78em; color:#8899AA; margin-left:6px;">'
                    f'portfolio avg</span><br/>'
                    f'<span style="font-size:0.78em; color:#5BE49B;">'
                    f'\u25b2 {_top["ticker"]} {_top["daily_change_pct"]:+.2f}%</span>'
                    f'&nbsp;&nbsp;'
                    f'<span style="font-size:0.78em; color:#FF6B6B;">'
                    f'\u25bc {_bot["ticker"]} {_bot["daily_change_pct"]:+.2f}%</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
        except Exception:
            pass

    # Sector breakdown (compact)
    with st.expander("Sector Breakdown", expanded=False):
        for sector in sectors:
            count = sum(1 for s in config._get_portfolio() if s["sector"] == sector)
            st.markdown(
                f'<span style="font-size:0.85em; color:#C8D6E5;">{sector} '
                f'<span style="color:#6B8299;">({count})</span></span>',
                unsafe_allow_html=True,
            )

    # Show current tickers
    with st.expander("Holdings", expanded=False):
        for stock in config._get_portfolio():
            _shares_str = f" ({stock['shares']} shares)" if stock.get("shares") else ""
            st.markdown(
                f'<span style="font-size:0.85em; color:#C8D6E5;">'
                f'<strong>{stock["ticker"]}</strong> — {stock["company_name"]}'
                f'<span style="color:#6B8299;">{_shares_str}</span></span>',
                unsafe_allow_html=True,
            )

    # Edit portfolio button
    if st.button("\u270f\ufe0f Edit Portfolio", use_container_width=True, key="edit_portfolio"):
        # Clear the portfolio so the onboarding form shows again
        st.session_state.pop("portfolio", None)
        st.cache_data.clear()
        st.rerun()

    st.divider()

    # ── Settings ──
    st.markdown(
        '<p style="font-size:0.75em; text-transform:uppercase; letter-spacing:0.08em; '
        "color:#6B8299; margin-bottom:4px; font-family:'Open Sans',sans-serif;\">Settings</p>",
        unsafe_allow_html=True,
    )

    # Cached-data toggle — judges can reproduce results without API costs
    if "use_cached" not in st.session_state:
        st.session_state["use_cached"] = False
    st.toggle("Use Cached Data", key="use_cached",
              help="When enabled, loads pre-cached data without API calls. "
                   "Useful for demonstrations and reproducibility.")

    st.divider()

    # ── Refresh ──
    if st.button("\U0001f504 Refresh All Data", use_container_width=True):
        with st.status("Refreshing all data...", expanded=True) as _sidebar_ref:
            _refresh_raw_caches(
                status_callback=lambda msg: _sidebar_ref.write(msg)
            )
            _sidebar_ref.update(label="Refresh complete!", state="complete")
        st.cache_data.clear()
        st.session_state["last_refreshed"] = datetime.now().strftime(
            "%H:%M:%S"
        )
    _last_ref = st.session_state.get("last_refreshed", "")
    if _last_ref:
        st.caption(f"Last refreshed at {_last_ref}")

    st.divider()

    # ── Footer ──
    st.markdown(
        '<div style="text-align:center; padding-top:8px;">'
        '<p style="font-size:0.72em; color:#6B8299; margin:0;">'
        'University of Nottingham &mdash; Group 28</p>'
        '<p style="font-size:0.72em; color:#6B8299; margin:0;">'
        'CFA Institute AI Investment Challenge 2025-26</p>'
        '<p style="font-size:0.65em; color:#4A6178; margin-top:6px;">'
        'Powered by Claude AI &bull; Streamlit &bull; yfinance</p>'
        '</div>',
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Main content — tabs
# ---------------------------------------------------------------------------
tab_holdings, tab_calendar, tab_events = st.tabs(
    ["\U0001f4c8 Holdings & Prices", "\U0001f4c5 Calendar & Dividends", "\u26a1 News & Events"]
)


# ---- Tab 1: Stock Holdings ------------------------------------------------
with tab_holdings:
    st.caption("Real-time price data, daily performance, and sector heatmap for your monitored holdings.")

    prices_loaded = False
    df = pd.DataFrame()

    try:
        if st.session_state.get("use_cached", False):
            # Use on-disk cached prices (no API call)
            cache_path = os.path.join(
                os.path.dirname(__file__), "data", "cache", "prices_cache.json"
            )
            try:
                df = _load_prices_cached(cache_path)
                prices_loaded = df["price"].notna().any()
            except (FileNotFoundError, Exception):
                st.warning("No cached data found. Fetching live data instead.")
                df = _fetch_prices_live(tuple(config.get_all_tickers()))
                prices_loaded = df["price"].notna().any()
        else:
            # Fetch live prices from yfinance (cached in memory for 5 min)
            df = _fetch_prices_live(tuple(config.get_all_tickers()))
            prices_loaded = df["price"].notna().any()

    except Exception:
        st.warning(
            "Unable to fetch live prices. Showing portfolio composition only."
        )
        # Fallback: show basic portfolio info without prices
        rows = [
            {
                "ticker": s["ticker"],
                "company_name": s["company_name"],
                "sector": s["sector"],
            }
            for s in config._get_portfolio()
        ]
        df = pd.DataFrame(rows)

    if prices_loaded:

        # Add sector from config for the display table
        df["sector"] = df["ticker"].apply(
            lambda t: (config.get_stock_by_ticker(t) or {}).get("sector", "")
        )

        # Rename columns for user-friendly display
        display_df = df.rename(
            columns={
                "ticker": "Ticker",
                "company_name": "Company",
                "sector": "Sector",
                "price": "Price (USD)",
                "daily_change_pct": "Daily Change (%)",
                "52w_high": "52W High",
                "52w_low": "52W Low",
            }
        )

        display_cols = [
            "Ticker", "Company", "Sector",
            "Price (USD)", "Daily Change (%)", "52W High", "52W Low",
        ]
        display_df = display_df[display_cols]

        # Colour the Daily Change column green (positive) / red (negative)
        def _colour_change(val):
            if val is None or pd.isna(val):
                return ""
            if val > 0:
                return "color: #28a745;"
            if val < 0:
                return "color: #dc3545;"
            return ""

        styled_df = display_df.style.map(
            _colour_change, subset=["Daily Change (%)"]
        ).format({
            "Price (USD)": "${:.2f}",
            "Daily Change (%)": "{:.2f}%",
            "52W High": "${:.2f}",
            "52W Low": "${:.2f}",
        })

        with st.container(border=True):
            st.dataframe(
                styled_df,
                use_container_width=True,
                hide_index=True,
            )

        # ------------------------------------------------------------------
        # Plotly horizontal bar chart — Daily Performance
        # ------------------------------------------------------------------
        st.subheader("Daily Performance")

        chart_df = df[["ticker", "daily_change_pct"]].copy()
        chart_df = chart_df.sort_values("daily_change_pct", ascending=True)
        # Colour bars green for gains, red for losses, grey for unchanged
        chart_df["colour"] = chart_df["daily_change_pct"].apply(
            lambda x: "#28a745" if x > 0 else "#dc3545" if x < 0 else "#6c757d"
        )

        fig = px.bar(
            chart_df,
            x="daily_change_pct",
            y="ticker",
            orientation="h",
            labels={"daily_change_pct": "Daily Change (%)", "ticker": ""},
            color="colour",
            color_discrete_map="identity",
        )
        fig.update_layout(
            showlegend=False,
            height=380,
            margin=dict(l=0, r=20, t=10, b=0),
            xaxis_tickformat=".2f",
            plot_bgcolor="rgba(0,0,0,0)",
        )
        fig.update_traces(
            hovertemplate="<b>%{y}</b><br>Change: %{x:.2f}%<extra></extra>"
        )

        with st.container(border=True):
            # Two-column layout: bar chart (left) + sector donut (right)
            _chart_left, _chart_right = st.columns([6, 4])
            with _chart_left:
                st.plotly_chart(fig, use_container_width=True)
            with _chart_right:
                # Sector exposure donut chart
                _sector_counts = {}
                for s in config._get_portfolio():
                    _sec = s.get("sector", "Other")
                    _sector_counts[_sec] = _sector_counts.get(_sec, 0) + 1
                # Dynamic colour palette — works with any sectors
                _palette = [
                    "#4299e1", "#48bb78", "#ed8936", "#f56565",
                    "#9f7aea", "#38b2ac", "#ecc94b", "#667eea",
                    "#fc8181", "#68d391", "#63b3ed", "#f6ad55",
                ]
                _sector_colours = {
                    s: _palette[i % len(_palette)]
                    for i, s in enumerate(_sector_counts.keys())
                }
                _donut_df = pd.DataFrame(
                    [{"Sector": k, "Count": v} for k, v in _sector_counts.items()]
                )
                _colour_seq = [
                    _sector_colours.get(s, "#a0aec0") for s in _donut_df["Sector"]
                ]
                _donut_fig = px.pie(
                    _donut_df,
                    values="Count",
                    names="Sector",
                    hole=0.55,
                    title="Sector Exposure",
                    color_discrete_sequence=_colour_seq,
                )
                _donut_fig.update_traces(textinfo="label+percent")
                _donut_fig.update_layout(
                    showlegend=False,
                    height=380,
                    margin=dict(l=0, r=0, t=40, b=0),
                )
                st.plotly_chart(_donut_fig, use_container_width=True)

            st.caption("Left: intraday % change (green = gain, red = loss). Right: sector allocation.")

    else:
        st.dataframe(df, use_container_width=True, hide_index=True)


# ---- Tab 2: Calendar ------------------------------------------------------
with tab_calendar:
    st.caption(
        "Scheduled events that may affect your portfolio holdings \u2014 "
        "macroeconomic releases, earnings dates, dividend dates, "
        "and recent SEC filings."
    )

    # Load data sources
    calendar_path = os.path.join(
        os.path.dirname(__file__), "data", "macro_calendar.json"
    )
    macro_events = []
    earnings_events = []
    filing_events = []
    dividend_events = []

    with st.spinner("Loading calendar data..."):
        try:
            macro_events = load_macro_calendar(calendar_path)
        except Exception as e:
            st.warning(f"Could not load macro calendar: {e}")

        try:
            # Use cached earnings fetcher (10-minute TTL)
            earnings_events = _fetch_earnings(
                tuple(config.get_all_tickers())
            )
        except Exception as e:
            st.warning(f"Could not fetch earnings dates: {e}")

        try:
            # Use cached dividend fetcher (10-minute TTL)
            dividend_events = _fetch_dividends(
                tuple(config.get_all_tickers())
            )
        except Exception as e:
            st.warning(f"Could not fetch dividend dates: {e}")

        filings_cache_path = os.path.join(
            os.path.dirname(__file__), "data", "cache", "filings_cache.json"
        )
        try:
            if os.path.exists(filings_cache_path):
                with open(filings_cache_path, "r") as f:
                    filing_events = json.load(f)
        except Exception as e:
            st.warning(f"Could not load filings cache: {e}")

    # Merge all events into a unified timeline
    try:
        all_events = merge_calendar_events(
            macro_events, earnings_events, filing_events, dividend_events
        )

        if all_events:
            # ---- Custom HTML Calendar Grid ----
            st.subheader("Calendar View")
            st.caption(
                "Events are colour-coded by type. "
                "Use the arrows to navigate between months."
            )

            # Month navigation via session state
            if "cal_year" not in st.session_state:
                st.session_state["cal_year"] = datetime.now().year
            if "cal_month" not in st.session_state:
                st.session_state["cal_month"] = datetime.now().month

            _nav_col1, _nav_col2, _nav_col3, _nav_col4, _nav_col5 = st.columns([1, 1, 4, 1, 1])
            with _nav_col1:
                if st.button("\u25c0 Prev", key="cal_prev", use_container_width=True):
                    m = st.session_state["cal_month"] - 1
                    if m < 1:
                        st.session_state["cal_month"] = 12
                        st.session_state["cal_year"] -= 1
                    else:
                        st.session_state["cal_month"] = m
                    st.rerun()
            with _nav_col2:
                if st.button("Today", key="cal_today", use_container_width=True):
                    st.session_state["cal_year"] = datetime.now().year
                    st.session_state["cal_month"] = datetime.now().month
                    st.rerun()
            with _nav_col3:
                _month_label = cal_module.month_name[st.session_state["cal_month"]]
                st.markdown(
                    f'<div class="cal-nav-title">{_month_label} {st.session_state["cal_year"]}</div>',
                    unsafe_allow_html=True,
                )
            with _nav_col5:
                if st.button("Next \u25b6", key="cal_next", use_container_width=True):
                    m = st.session_state["cal_month"] + 1
                    if m > 12:
                        st.session_state["cal_month"] = 1
                        st.session_state["cal_year"] += 1
                    else:
                        st.session_state["cal_month"] = m
                    st.rerun()

            # Render the HTML calendar grid
            with st.container(border=True):
                _cal_html = _build_calendar_html(
                    st.session_state["cal_year"],
                    st.session_state["cal_month"],
                    all_events,
                )
                st.markdown(_cal_html, unsafe_allow_html=True)

            # Colour legend
            legend_cols = st.columns(5)
            legend_cols[0].markdown(
                '<span style="display:inline-block;width:12px;height:12px;'
                'border-radius:50%;background:#F59E0B;margin-right:6px;vertical-align:middle;"></span>'
                '**Macro**', unsafe_allow_html=True)
            legend_cols[1].markdown(
                '<span style="display:inline-block;width:12px;height:12px;'
                'border-radius:50%;background:#C084FC;margin-right:6px;vertical-align:middle;"></span>'
                '**Earnings**', unsafe_allow_html=True)
            legend_cols[2].markdown(
                '<span style="display:inline-block;width:12px;height:12px;'
                'border-radius:50%;background:#3B82F6;margin-right:6px;vertical-align:middle;"></span>'
                '**Filing**', unsafe_allow_html=True)
            legend_cols[3].markdown(
                '<span style="display:inline-block;width:12px;height:12px;'
                'border-radius:50%;background:#22C55E;margin-right:6px;vertical-align:middle;"></span>'
                '**Dividend**', unsafe_allow_html=True)
            legend_cols[4].markdown(
                '<span style="display:inline-block;width:12px;height:12px;'
                'border-radius:50%;background:#86EFAC;margin-right:6px;vertical-align:middle;"></span>'
                '**Ex-Dividend**', unsafe_allow_html=True)

            st.divider()

            # ---- Data table below the calendar ----
            st.subheader("Event Details")

            cal_df = pd.DataFrame(all_events)
            cal_df["date"] = pd.to_datetime(cal_df["date"], errors="coerce")
            cal_df = cal_df.dropna(subset=["date"])

            # Only show events from today onwards
            cal_df = cal_df[
                cal_df["date"] >= pd.Timestamp.today().normalize()
            ]
            cal_df = cal_df.sort_values("date")

            cal_df["Affected Sectors"] = cal_df["affected_sectors"].apply(
                lambda x: ", ".join(x) if isinstance(x, list) else str(x)
            )

            # Emoji prefix for event type
            type_emoji = {
                "macro": "\U0001f3db\ufe0f Macro",
                "earnings": "\U0001f4ca Earnings",
                "filing": "\U0001f4c4 Filing",
                "dividend": "\U0001f4b0 Dividend",
                "ex-dividend": "\U0001f4c5 Ex-Dividend",
            }
            cal_df["Type"] = cal_df["event_type"].apply(
                lambda x: type_emoji.get(x, x)
            )

            display_cal = cal_df.rename(
                columns={
                    "date": "Date",
                    "event_name": "Event",
                    "description": "Description",
                }
            )[["Date", "Type", "Event", "Description", "Affected Sectors"]]

            display_cal["Date"] = display_cal["Date"].dt.strftime("%Y-%m-%d")

            st.dataframe(
                display_cal, use_container_width=True, hide_index=True
            )

            st.caption(
                f"Showing {len(display_cal)} upcoming events "
                f"({len([e for e in all_events if e.get('event_type') == 'macro'])} macro, "
                f"{len(earnings_events)} earnings, "
                f"{len(filing_events)} filings loaded)"
            )
        else:
            st.info("No upcoming events found.")

    except Exception as e:
        st.error(f"Error merging calendar data: {e}")
        # Fallback: show just macro events if available
        if macro_events:
            st.markdown("**Showing macro events only (fallback):**")
            cal_df = pd.DataFrame(macro_events)
            cal_df["date"] = pd.to_datetime(cal_df["date"])
            cal_df = cal_df[
                cal_df["date"] >= pd.Timestamp.today().normalize()
            ]
            cal_df = cal_df.sort_values("date")
            cal_df["Affected Sectors"] = cal_df["affected_sectors"].apply(
                lambda x: ", ".join(x) if isinstance(x, list) else str(x)
            )
            display_cal = cal_df.rename(
                columns={
                    "date": "Date",
                    "event_name": "Event",
                    "description": "Description",
                }
            )[["Date", "Event", "Description", "Affected Sectors"]]
            display_cal["Date"] = display_cal["Date"].dt.strftime("%Y-%m-%d")
            st.dataframe(
                display_cal, use_container_width=True, hide_index=True
            )


# ---- Tab 3: Events (Dual News Feed) --------------------------------------
with tab_events:
    st.caption(
        "AI-curated news and developments most relevant to your portfolio "
        "holdings. Each item is scored for portfolio impact and summarised "
        "by Claude."
    )

    # ── Events toolbar: model selector + refresh ──
    _ev_col_spacer, _ev_col_model, _ev_col_refresh = st.columns([5, 2, 1.2])
    with _ev_col_model:
        _ev_model = st.selectbox(
            "AI Model",
            ["Haiku (Fast)", "Sonnet (Advanced)"],
            index=0,
            help="Haiku is faster and cheaper; Sonnet produces richer, more nuanced analysis.",
            label_visibility="collapsed",
        )
        _model_map = {
            "Haiku (Fast)": "claude-haiku-4-5-20251001",
            "Sonnet (Advanced)": "claude-sonnet-4-6",
        }
        st.session_state["llm_model"] = _model_map[_ev_model]
    with _ev_col_refresh:
        if st.button("\U0001f504 Refresh", use_container_width=True, key="refresh_events"):
            # Re-fetch all raw data, then clear event caches so generators
            # rebuild from fresh data. This replaces the manual
            # `python refresh_caches.py` step.
            with st.status("Refreshing live data...", expanded=True) as _ref_status:
                _refresh_raw_caches(
                    status_callback=lambda msg: _ref_status.write(msg)
                )
                _ref_status.update(
                    label="Data refreshed — rebuilding events...",
                    state="complete",
                )
            st.cache_data.clear()
            st.session_state["last_refreshed"] = datetime.now().strftime("%H:%M:%S")
            st.rerun()

    use_cache = st.session_state.get("use_cached", False)

    # ------------------------------------------------------------------
    # Generate all three feeds with collapsible status indicator
    # ------------------------------------------------------------------
    daily_events = []
    reactive_events = []
    sector_events = []

    with st.status("Analysing portfolio news...", expanded=False) as _ai_status:
        _ai_status.update(label="Fetching daily headlines (last 48 hours)...")
        try:
            daily_events = _cached_daily_reactive_events(
                max_events=10, use_cache=use_cache
            )
        except Exception as e:
            st.warning(f"Could not generate daily feed: {e}")
        _ai_status.write(f"Daily headlines: {len(daily_events)} events found")

        _ai_status.update(label="Scoring 14-day intelligence feed...")
        try:
            reactive_events = _cached_reactive_events(
                max_events=10, use_cache=use_cache
            )
        except Exception as e:
            st.warning(f"Could not generate 14-day feed: {e}")
        _ai_status.write(f"14-day feed: {len(reactive_events)} events scored")

        _ai_status.update(label="Scanning sector developments...")
        try:
            sector_events = _cached_sector_events(
                max_events=8, use_cache=use_cache
            )
        except Exception as e:
            st.warning(f"Could not generate sector feed: {e}")
        _ai_status.write(f"Sector intelligence: {len(sector_events)} events found")

        _ai_status.update(
            label=f"Analysis complete — {len(daily_events) + len(reactive_events) + len(sector_events)} total events",
            state="complete",
            expanded=False,
        )

    # ------------------------------------------------------------------
    # Side-by-side dual feed layout
    # ------------------------------------------------------------------
    col_daily, col_14day = st.columns(2)

    # ---- Left column: Today's Headlines (48 hours) ----
    with col_daily:
      with st.container(border=True):
        st.subheader("Today's Headlines")
        st.caption("Last 48 hours — latest developments")

        if daily_events:
            for ev in daily_events:
                tier = ev.get("relevance_tier", "Macro")
                tickers = ev.get("affected_tickers", [])
                if isinstance(tickers, str):
                    tickers_display = tickers
                elif isinstance(tickers, list):
                    tickers_display = ", ".join(tickers) if tickers else "N/A"
                else:
                    tickers_display = "N/A"

                source_name = ev.get("source", "")
                ev_title = ev.get("title", "Unknown Event")
                if not source_name and " - " in ev_title:
                    source_name = ev_title.rsplit(" - ", 1)[-1].strip()

                render_event_card(
                    title=ev_title,
                    relevance_tier=tier,
                    tickers=tickers_display,
                    summary=ev.get(
                        "summary",
                        ev.get("description", "No summary available."),
                    ),
                    source_url=ev.get("source_url", ""),
                    timestamp=str(
                        ev.get("timestamp", ev.get("date", ""))
                    ),
                    source_name=source_name,
                    mention_count=ev.get("mention_count", 1),
                    sentiment=ev.get("ai_sentiment", ""),
                    centrality=ev.get("entity_centrality", ""),
                )
        else:
            st.info(
                "No headlines from the last 48 hours. This can happen if "
                "markets are closed or if the news cache needs refreshing.\n\n"
                "Click **Refresh Data** in the sidebar to re-fetch."
            )

    # ---- Right column: 14-Day Intelligence ----
    with col_14day:
      with st.container(border=True):
        st.subheader("14-Day Intelligence")
        st.caption("Two-week analysis — broader trends and developments")

        if reactive_events:
            for ev in reactive_events:
                tier = ev.get("relevance_tier", "Macro")
                tickers = ev.get("affected_tickers", [])
                if isinstance(tickers, str):
                    tickers_display = tickers
                elif isinstance(tickers, list):
                    tickers_display = ", ".join(tickers) if tickers else "N/A"
                else:
                    tickers_display = "N/A"

                source_name = ev.get("source", "")
                ev_title = ev.get("title", "Unknown Event")
                if not source_name and " - " in ev_title:
                    source_name = ev_title.rsplit(" - ", 1)[-1].strip()

                render_event_card(
                    title=ev_title,
                    relevance_tier=tier,
                    tickers=tickers_display,
                    summary=ev.get(
                        "summary",
                        ev.get("description", "No summary available."),
                    ),
                    source_url=ev.get("source_url", ""),
                    timestamp=str(
                        ev.get("timestamp", ev.get("date", ""))
                    ),
                    source_name=source_name,
                    mention_count=ev.get("mention_count", 1),
                    sentiment=ev.get("ai_sentiment", ""),
                    centrality=ev.get("entity_centrality", ""),
                )
        else:
            st.info(
                "No news items available. To see the feed, try one of these:\n\n"
                "- Ensure `data/cache/news_cache.json` exists (run `python refresh_caches.py`).\n"
                "- Ensure your `.env` file contains a valid `ANTHROPIC_API_KEY`.\n"
                "- Click **Refresh Data** to re-fetch from live sources."
            )

    # ------------------------------------------------------------------
    # Sector Intelligence section (full width, below the two columns)
    # ------------------------------------------------------------------
    st.divider()
    with st.container(border=True):
        st.subheader("Sector Intelligence")
        st.caption(
            "Industry-wide developments affecting portfolio sectors — "
            "regulatory shifts, macro-to-sector impacts, and competitive landscape"
        )

        if sector_events:
            # Display in two columns for visual balance
            col_sec_left, col_sec_right = st.columns(2)
            for idx, ev in enumerate(sector_events):
                tier = ev.get("relevance_tier", "Sector")
                tickers = ev.get("affected_tickers", [])
                if isinstance(tickers, str):
                    tickers_display = tickers
                elif isinstance(tickers, list):
                    tickers_display = ", ".join(tickers) if tickers else "N/A"
                else:
                    tickers_display = "N/A"

                source_name = ev.get("source", "")
                ev_title = ev.get("title", "Unknown Event")
                if not source_name and " - " in ev_title:
                    source_name = ev_title.rsplit(" - ", 1)[-1].strip()

                # Sector origin label
                sector_origin = ev.get("sector_origin", "")
                if sector_origin:
                    ev_title_display = ev_title
                else:
                    ev_title_display = ev_title

                target_col = col_sec_left if idx % 2 == 0 else col_sec_right
                with target_col:
                    render_event_card(
                        title=ev_title_display,
                        relevance_tier=tier,
                        tickers=tickers_display,
                        summary=ev.get(
                            "summary",
                            ev.get("description", "No summary available."),
                        ),
                        source_url=ev.get("source_url", ""),
                        timestamp=str(
                            ev.get("timestamp", ev.get("date", ""))
                        ),
                        source_name=source_name,
                        mention_count=ev.get("mention_count", 1),
                        sentiment=ev.get("ai_sentiment", ""),
                        centrality=ev.get("entity_centrality", ""),
                    )
        else:
            st.info(
                "No sector news available. Run `python refresh_caches.py` to "
                "fetch sector-level news, then click **Refresh Data**."
            )
