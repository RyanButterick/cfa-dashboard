# PACED Dashboard

**Portfolio-Aware Catalyst & Event Intelligence Dashboard**

An AI-powered investment intelligence dashboard that monitors a user-defined equity portfolio and surfaces the most relevant market events, news catalysts, and scheduled releases. Built for smaller investment offices and retail investors who need institutional-quality event awareness without expensive terminal subscriptions.

Built for the **CFA Institute AI Investment Challenge 2025-2026** by University of Nottingham, Group 28.

## Features

- **Holdings & Prices** — Real-time price data, daily performance bar chart, and sector exposure donut chart for your monitored holdings via Yahoo Finance.
- **Calendar & Dividends** — Unified timeline of macroeconomic releases, earnings dates, dividend/ex-dividend dates, and SEC filings rendered in a colour-coded interactive calendar.
- **News & Events (AI-Curated)** — Three AI-powered intelligence feeds:
  - *Today's Headlines* — Last 48 hours of news, scored and summarised by Claude for portfolio relevance.
  - *14-Day Intelligence* — Two-week analysis capturing broader trends and developments.
  - *Sector Intelligence* — Industry-wide developments affecting portfolio sectors.

## How It Works

The dashboard uses a multi-stage AI processing pipeline:

1. **Ingestion** — Fetches news from Google News RSS and Yahoo Finance RSS (dual queries per ticker: symbol + company name), SEC filings from EDGAR, and earnings/dividend dates from yfinance.
2. **Mover-Enhanced Search** — Stocks with significant daily moves (>3%) trigger tiered additional searches to find the catalyst driving the move.
3. **Keyword Pre-Filter** — Fast rule-based classifier tags articles by relevance tier (Direct, Sector, Macro) and removes obvious noise.
4. **AI Batch Scoring** — Remaining headlines are sent to Anthropic's Claude API in batches of 25. Each is scored 1-10 for portfolio relevance, with affected tickers and sentiment identified.
5. **Deduplication & Ranking** — Semantic deduplication collapses same-story coverage, intra-ticker clustering limits per-stock noise, and a 7-factor composite score ranks the final output.
6. **AI Summarisation** — Top-scoring events receive full AI-generated summaries explaining what happened and why it matters to the portfolio.

## Installation

### Prerequisites

- Python 3.10+
- An Anthropic API key (for AI features — [get one here](https://console.anthropic.com/))

### Setup

1. Clone this repository:

```bash
git clone https://github.com/your-username/cfa-dashboard.git
cd cfa-dashboard
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Create your environment file:

```bash
cp .env.example .env
```

Then edit `.env` and add your Anthropic API key:

```
ANTHROPIC_API_KEY=your_key_here
```

4. Run the dashboard:

```bash
streamlit run app.py
```

The dashboard will open in your browser. On first launch, you'll be prompted to configure your portfolio (select tickers, and enter share counts).

There is an option to use demo portfolio - This will generate the 10 stocks that I used in the production of the portfolio and is merely for convenience, if you don't want to use an API key, make sure to use the demo portfolio as all cached data is for the demo portfolio and was computed on 09/04/26.

Instead, you can select up to 10 **U.S.-listed companies**. Just input 10 tickers you are curious about, and the software will detect if you have mistyped any tickers automatically - the events tab will only work if you have added your Anthropic API to the .env file.

*IMPORTANT* Once you've selected your portfolio, hit "refresh all data" in the sidebar. This will update the entire dashboard and may take 1-2 minutes, depending on the internet connection and computing power.

I recommend using VS Code to see the process of event filtering. Usually, ~600 Headlines will be analysed and filtered down with ~200 passing the initial scoring system. Claude's Haiku model will then give an advanced score to each event using criteria we have outlined in the code. This should show the mose "relevant" events surrounding the selected portfolio. 

The analysis process does take a few minutes and is dependent on: internet connection, compute power, how volatile the stocks are, quantity/quality of news surrounding the stocks, how many headlines passed the filtering system, and other factors. We recognise the slowness of the process and are currently developing a more efficient filtering system using a python script (metatags) instead of fully AI. 

## Using an API key##

1. We **highly recommend** using an API key. This will allow you to experience the full capabilities of our program.
2. To get an API key, it is important your Anthropic account has credits before you generate the key. You can purchase them here:                                     https://platform.claude.com/settings/billing
3. Once you have credits, create an API key here: https://platform.claude.com/settings/keys
4. Copy your API key and replace "your_key_here" in the .env file.
5. It shoud look like: ANTHROPIC_API_KEY=sk-ant-api03-jYndRTrvMKU-Wigm5GuD3loDawkJDhPFlmSa79ws5obm7smZITPaJRWawd_aed333dsd3WtCrw-M65IOgAA
6. Now run streamlit run app.py in your terminal, create a portfolio, refresh all data (make sure cached data is unchecked), wait a few minutes and voila.
   
## Using Cached Data (No API Key Required)

To run the dashboard without making live API calls:

1. Toggle **"Use Cached Data"** in the sidebar.
2. The app will load pre-saved data from the `data/cache/` directory.
3. All three intelligence feeds will display using previously generated AI analysis.

This is useful for reproducibility, demonstrations, and running without an API key. The cached data represents a snapshot of the dashboard's output and does not require any external API calls. When using cached data, a full AI summary when clicking each event's dropdown isn't displayed, it will just show the title again. This was done for simplicity reasons as the output is merely a demonstration of what the product is capable of. To see full AI summary's of events, add an Anthropic API key to the .env file and refresh the page using the tool in the sidebar. This should take a couple minutes but will update the full dashboard as well as full AI summaries and catalyst suggestions.

## Refreshing Data

Click the **Refresh** button on the News & Events tab to re-fetch all data:

- Live prices from Yahoo Finance
- News headlines from Google News RSS + Yahoo Finance RSS
- Enhanced mover search for volatile stocks (tiered: 3%/4%/5% thresholds)
- SEC filings from EDGAR
- Sector-level news

This replaces the need to run `refresh_caches.py` manually.

## Data Sources

| Source | What It Provides | API Key Required? |
|--------|-----------------|-------------------|
| Yahoo Finance (via yfinance) | Stock prices, earnings dates, dividend dates | No |
| Google News RSS | News headlines (dual query: ticker + company name) | No |
| Yahoo Finance RSS | Curated per-stock news feeds | No |
| SEC EDGAR | 8-K, 10-K, 10-Q filings | No |
| Macro Calendar | Economic releases (CPI, NFP, FOMC, GDP) | No (manually curated JSON) |
| Anthropic Claude API | AI scoring, classification, and summarisation | Yes |

## Project Structure

```
cfa-dashboard/
├── app.py                     # Main Streamlit dashboard
├── config.py                  # Portfolio configuration & session state
├── refresh_caches.py          # CLI tool to refresh all data caches
├── requirements.txt           # Python dependencies
├── .env.example               # Environment variable template
├── data/
│   ├── macro_calendar.json    # Curated macroeconomic event calendar
│   └── cache/                 # Cached data for offline/reproducible use
├── src/
│   ├── ingestion/
│   │   ├── news.py            # Google News RSS + Yahoo Finance RSS fetching
│   │   ├── prices.py          # yfinance price data
│   │   ├── edgar.py           # SEC EDGAR filing fetcher
│   │   └── calendar_data.py   # Calendar event merging & normalisation
│   ├── processing/
│   │   ├── classifier.py      # Keyword-based relevance classification
│   │   ├── event_generator.py # Multi-stage AI pipeline orchestration
│   │   └── ranker.py          # Composite scoring & commentary detection
│   ├── llm/
│   │   ├── summariser.py      # Claude API client & batch scoring
│   │   └── prompts.py         # AI prompt templates
│   └── portfolio_setup.py     # Onboarding UI for portfolio configuration
├── tests/                     # Unit tests
├── docs/                      # Documentation
└── LICENSE                    # MIT License
```

## AI Model Selection

The dashboard supports two Claude models, selectable from the News & Events tab:

- **Haiku (Fast)** — Lower cost, faster responses. Suitable for routine monitoring.
- **Sonnet (Advanced)** — Richer analysis with more nuanced scoring. Better for detailed research.

## Estimated API Costs

A single full refresh (all three feeds) typically costs $0.01-0.03 with Haiku, or $0.05-0.15 with Sonnet. Monthly costs for daily use would be approximately $0.50-1.00 with Haiku.

## Tech Stack

- **Python 3.10+** with Streamlit for the web interface
- **yfinance** for market data
- **feedparser** for RSS ingestion
- **Anthropic Claude API** for AI scoring and summarisation
- **Plotly** for interactive charts
- **pandas** for data processing

## Team

University of Nottingham — MSc Finance and Investment — Group 28

## License

MIT License — see [LICENSE](LICENSE) file for details.

## AI Disclosure

Generative AI tools (Claude by Anthropic) were used during development to assist with code generation, debugging, and documentation. Claude API is also integrated into the solution for event classification, relevance scoring, and summarisation. All AI-generated content in the dashboard is clearly labelled and the system can operate in cached mode without any AI calls. Full details of AI usage are documented in the Technical Explanation.
