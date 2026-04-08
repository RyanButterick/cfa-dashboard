"""Portfolio onboarding form for dynamic stock selection.

Provides a clean setup screen where users enter up to 10 US stock tickers
and share quantities. Validates each ticker against yfinance and auto-fetches
company metadata (name, sector, sub-industry).

Used by app.py when no portfolio is configured in session state.
"""

import streamlit as st
import yfinance as yf


# ---------------------------------------------------------------------------
# Sector-level exposure tags — auto-generated from sector name
# ---------------------------------------------------------------------------
_SECTOR_TAG_MAP = {
    "Technology": ["technology", "software", "cloud computing", "AI", "semiconductor"],
    "Information Technology": ["technology", "software", "cloud computing", "AI", "semiconductor"],
    "Financials": ["interest rates", "banking", "credit", "Fed policy", "regulation"],
    "Healthcare": ["pharmaceuticals", "FDA approval", "medical devices", "drug pricing"],
    "Health Care": ["pharmaceuticals", "FDA approval", "medical devices", "drug pricing"],
    "Energy": ["oil price", "OPEC", "energy policy", "natural gas", "carbon"],
    "Consumer Discretionary": ["consumer spending", "e-commerce", "retail", "advertising"],
    "Consumer Staples": ["consumer staples", "pricing power", "inflation impact"],
    "Industrials": ["infrastructure", "construction", "manufacturing", "commodity prices"],
    "Utilities": ["utility regulation", "renewable energy", "power demand", "interest rates"],
    "Materials": ["commodity prices", "mining", "chemicals", "raw materials"],
    "Real Estate": ["interest rates", "housing market", "REIT", "commercial property"],
    "Communication Services": ["media", "advertising", "streaming", "telecom"],
}


def _fetch_stock_info(ticker: str) -> dict | None:
    """Validate a ticker and fetch its metadata from yfinance.

    Args:
        ticker: US stock ticker symbol (e.g. 'AAPL').

    Returns:
        Dict with company_name, sector, sub_sector, geography keys,
        or None if the ticker is invalid.
    """
    try:
        t = yf.Ticker(ticker.upper().strip())
        info = t.info
        # yfinance returns an empty or minimal dict for invalid tickers
        name = info.get("longName") or info.get("shortName") or ""
        sector = info.get("sector") or ""
        industry = info.get("industry") or ""

        if not name or not sector:
            return None

        return {
            "company_name": name,
            "sector": sector,
            "sub_sector": industry,
            "geography": "US",
        }
    except Exception:
        return None


def _build_portfolio_entry(ticker: str, shares: int, info: dict) -> dict:
    """Build a portfolio entry dict from ticker, shares, and yfinance info.

    Args:
        ticker: Uppercase ticker symbol.
        shares: Number of shares held.
        info: Dict from _fetch_stock_info().

    Returns:
        Complete portfolio entry dict matching config.py format.
    """
    sector = info.get("sector", "")
    # Generate exposure tags from sector + sub-industry keywords
    tags = list(_SECTOR_TAG_MAP.get(sector, [f"{sector.lower()} sector"]))
    # Add sub-industry keywords
    sub = info.get("sub_sector", "")
    if sub:
        for word in sub.lower().split():
            if len(word) > 3 and word not in tags:
                tags.append(word)

    return {
        "ticker": ticker.upper(),
        "company_name": info["company_name"],
        "sector": sector,
        "sub_sector": info.get("sub_sector", ""),
        "geography": info.get("geography", "US"),
        "shares": shares,
        "exposure_tags": tags,
    }


def render_onboarding_form() -> bool:
    """Render the portfolio setup form. Returns True if portfolio was submitted.

    Displays a clean onboarding screen with:
    - PACED branding logo
    - Title and instructions
    - Up to 10 ticker/shares input rows
    - Validation against yfinance
    - Demo mode button to load defaults

    Returns:
        True if a portfolio was successfully configured (triggers rerun).
    """
    # ── Global CSS: Open Sans, dark sidebar, setup header ──
    st.markdown(
        """<style>
        @import url('https://fonts.googleapis.com/css2?family=Open+Sans:wght@400;500;600;700;800&display=swap');

        html, body, [class*="css"], .stMarkdown, .stDataFrame, .stMetric,
        .stTabs, .stExpander, .stSelectbox, .stButton, h1, h2, h3, h4, h5, h6, p, span, div {
            font-family: 'Open Sans', -apple-system, BlinkMacSystemFont, sans-serif !important;
        }

        /* Dark sidebar */
        section[data-testid="stSidebar"] {
            background-color: #1B2A4A !important;
        }
        section[data-testid="stSidebar"] * {
            color: #C8D6E5 !important;
        }
        section[data-testid="stSidebar"] hr {
            border-color: rgba(255,255,255,0.12) !important;
        }

        /* Hide Streamlit chrome */
        #MainMenu {visibility: hidden;}
        footer {visibility: hidden;}
        header[data-testid="stHeader"] {background: transparent;}
        section[data-testid="stSidebar"] [data-testid="stSidebarNav"] {
            display: none !important;
        }

        .setup-header { text-align: center; padding: 20px 0 10px 0; }
        .setup-header p { color: #4F7C82; font-size: 1.05em; font-family: 'Open Sans', sans-serif; }
        .setup-subtitle {
            font-size: 0.82em; font-weight: 600; color: #0B2E33;
            font-family: 'Open Sans', sans-serif; margin-top: 6px;
        }
        .setup-credit {
            font-size: 0.72em; color: #4F7C82;
            font-family: 'Open Sans', sans-serif; margin-top: 4px;
        }
        </style>""",
        unsafe_allow_html=True,
    )

    # ── PACED branding in sidebar ──
    with st.sidebar:
        st.markdown(
            '<div style="padding:18px 0 10px 0; text-align:center;">'
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 260 60" '
            'style="width:180px; height:auto; margin-bottom:6px;">'
            '<circle cx="30" cy="30" r="26" fill="none" stroke="#FFFFFF" stroke-width="2"/>'
            '<rect x="17" y="34" width="6" height="12" rx="1" fill="#FFFFFF"/>'
            '<rect x="27" y="26" width="6" height="20" rx="1" fill="#FFFFFF"/>'
            '<rect x="37" y="18" width="6" height="28" rx="1" fill="#FFFFFF"/>'
            '<path d="M16 36 Q24 28 30 26 Q36 24 44 17" fill="none" '
            'stroke="#FFFFFF" stroke-width="2" stroke-linecap="round"/>'
            "<text x='68' y='38' fill='#FFFFFF' font-family='Open Sans, sans-serif' "
            "font-size='28' font-weight='700' letter-spacing='3'>PACED</text>"
            '</svg>'
            '<div style="font-size:0.72em; color:#C8D6E5; margin-top:2px; '
            "font-family:'Open Sans',sans-serif;\">"
            'Portfolio-Aware Catalyst &amp; Event<br/>Intelligence Dashboard</div>'
            '<div style="font-size:0.62em; color:#6B8299; margin-top:4px; '
            "font-family:'Open Sans',sans-serif;\">"
            'University of Nottingham &mdash; Group 28</div>'
            '</div>',
            unsafe_allow_html=True,
        )
        st.divider()
        st.markdown(
            '<p style="font-size:0.78em; color:#8899AA; text-align:center; '
            "font-family:'Open Sans',sans-serif; padding:8px 0;\">"
            'Configure your portfolio to get started.</p>',
            unsafe_allow_html=True,
        )

    # ── Main content: PACED logo + setup form ──
    st.markdown(
        '<div class="setup-header">'
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 260 60" '
        'style="width:220px; height:auto; margin-bottom:10px;">'
        '<circle cx="30" cy="30" r="26" fill="none" stroke="#0B2E33" stroke-width="2"/>'
        '<rect x="17" y="34" width="6" height="12" rx="1" fill="#0B2E33"/>'
        '<rect x="27" y="26" width="6" height="20" rx="1" fill="#0B2E33"/>'
        '<rect x="37" y="18" width="6" height="28" rx="1" fill="#0B2E33"/>'
        '<path d="M16 36 Q24 28 30 26 Q36 24 44 17" fill="none" '
        'stroke="#4F7C82" stroke-width="2" stroke-linecap="round"/>'
        "<text x='68' y='38' fill='#0B2E33' font-family='Open Sans, sans-serif' "
        "font-size='28' font-weight='700' letter-spacing='3'>PACED</text>"
        '</svg>'
        '<div class="setup-subtitle">'
        'Portfolio-Aware Catalyst &amp; Event Intelligence Dashboard</div>'
        '<div class="setup-credit">'
        'University of Nottingham &mdash; Group 28 &nbsp;|&nbsp; '
        'CFA Institute AI Investment Challenge 2025-26</div>'
        '</div>',
        unsafe_allow_html=True,
    )

    st.divider()

    st.markdown("### Configure Your Portfolio")
    st.markdown(
        "Enter up to **10 US stock tickers** and the number of shares you hold. "
        "The dashboard will automatically fetch company details, sector data, "
        "and begin monitoring news and events for your portfolio."
    )

    st.info(
        "Tip: Type the ticker symbol exactly as it appears on the NYSE/NASDAQ "
        "(e.g. **AAPL**, **MSFT**, **TSLA**). We'll validate each one."
    )

    # Initialize form state
    if "setup_rows" not in st.session_state:
        st.session_state["setup_rows"] = 3  # start with 3 rows

    num_rows = st.session_state["setup_rows"]

    # Input grid
    tickers_input = []
    shares_input = []

    for i in range(num_rows):
        col_tick, col_shares = st.columns([3, 2])
        with col_tick:
            t = st.text_input(
                f"Ticker {i + 1}",
                key=f"setup_ticker_{i}",
                placeholder="e.g. AAPL",
                max_chars=10,
            )
            tickers_input.append(t.strip().upper() if t else "")
        with col_shares:
            s = st.number_input(
                f"Shares {i + 1}",
                key=f"setup_shares_{i}",
                min_value=0,
                value=0,
                step=1,
            )
            shares_input.append(int(s))

    # Add more rows button
    if num_rows < 10:
        if st.button(f"+ Add another stock (up to 10)", key="add_row"):
            st.session_state["setup_rows"] = min(num_rows + 1, 10)
            st.rerun()

    st.divider()

    # Submit buttons
    col_submit, col_demo = st.columns(2)

    with col_submit:
        if st.button(
            "Build My Dashboard",
            type="primary",
            use_container_width=True,
            key="submit_portfolio",
        ):
            # Filter to non-empty tickers
            entries = [
                (t, s) for t, s in zip(tickers_input, shares_input) if t
            ]

            if not entries:
                st.error("Please enter at least one ticker symbol.")
                return False

            # Check for duplicates
            seen = set()
            unique_entries = []
            for t, s in entries:
                if t not in seen:
                    seen.add(t)
                    unique_entries.append((t, s))

            # Validate all tickers
            portfolio = []
            progress = st.progress(0, text="Validating tickers...")

            for idx, (ticker, shares) in enumerate(unique_entries):
                progress.progress(
                    (idx + 1) / len(unique_entries),
                    text=f"Looking up {ticker}...",
                )
                info = _fetch_stock_info(ticker)
                if info is None:
                    st.error(
                        f"**{ticker}** is not a valid US stock ticker. "
                        "Please check the symbol and try again."
                    )
                    return False

                portfolio.append(_build_portfolio_entry(ticker, shares, info))

            progress.progress(1.0, text="Portfolio configured!")

            # Store in session state via config
            import config
            config.set_portfolio(portfolio)
            st.rerun()
            return True

    with col_demo:
        if st.button(
            "Use Demo Portfolio (10 stocks)",
            use_container_width=True,
            key="demo_portfolio",
        ):
            import config
            config.set_portfolio(config.get_default_portfolio())
            st.rerun()
            return True

    return False
