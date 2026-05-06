# Free Market News Prototype

A no-paid-API stock market news fetcher prototype.

Location:

    E:\Code\free-market-news

WSL path:

    /mnt/e/Code/free-market-news

## What it does

The prototype fetches and stores market news from free sources:

- Yahoo Finance RSS per ticker
- Google News RSS per ticker/company
- Google News RSS for broad market topics
- General RSS feeds from CNBC, MarketWatch, and Yahoo Finance
- Optional SEC EDGAR recent filing notices, if you provide a SEC User-Agent

It deduplicates URLs in SQLite, scores articles by high-signal keywords, and prints only newly saved items.

No paid API key is needed. No third-party Python packages are required.

## Files

- `Open Free Market News.bat` - one-click app launcher in the project root
- `app/newsbot.py` - main CLI app and library functions
- `app/webapp.py` - local web dashboard
- `app/portfolio.json` - starter portfolio/watchlist
- `data/news.db` - saved article database
- `tests/test_newsbot.py` - unittest coverage

## Quick start

From Windows, double-click:

    Open Free Market News.bat

That starts the local server and opens the dashboard in your browser.

From WSL:

    cd /mnt/e/Code/free-market-news
    python3 app/newsbot.py --portfolio app/portfolio.json --db data/news.db --limit 30

From Windows PowerShell, if Python is available there:

    cd E:\Code\free-market-news
    python app\newsbot.py --portfolio app\portfolio.json --db data\news.db --limit 30

## Web UI

Start the local dashboard:

    cd E:\Code\free-market-news
    python app\launcher.py

Then open:

    http://127.0.0.1:8765

The UI shows saved articles from SQLite, lets you filter by score/feed/tag/search, and has a Fetch latest button that runs the same free RSS fetcher in the background.

## Useful commands

Fetch a small sample:

    python3 app/newsbot.py --portfolio app/portfolio.json --db data/news.db --max-feeds 6 --limit 20 --verbose

Only show higher-signal items:

    python3 app/newsbot.py --portfolio app/portfolio.json --db data/news.db --min-score 3 --limit 20

Skip broad market feeds and only use portfolio-derived feeds:

    python3 app/newsbot.py --portfolio app/portfolio.json --db data/news.db --no-general

Show fetch errors:

    python3 app/newsbot.py --portfolio app/portfolio.json --db data/news.db --show-errors

Enable SEC EDGAR filings:

    python3 app/newsbot.py --portfolio app/portfolio.json --db data/news.db --sec-user-agent "Your Name your.email@example.com"

SEC requires a real descriptive User-Agent with contact info. Replace the example before using it heavily.

## Portfolio format

`portfolio.json` can be either an object:

    {
      "NVDA": "Nvidia",
      "MSFT": "Microsoft"
    }

or a list:

    ["NVDA", "MSFT", "PLTR"]

The object format is better because Google News searches can use both company name and ticker.

## How scoring works

The prototype adds score points for high-signal categories:

- earnings
- guidance
- analyst upgrades/downgrades/price targets
- SEC filings
- M&A, contracts, partnerships
- lawsuits/investigations/regulation/offering/debt
- buybacks/dividends

It downranks obvious low-signal article patterns like:

- should you buy
- millionaire-maker
- no-brainer
- could soar
- 3 stocks
- Zacks rank

This is intentionally simple and easy to edit in `newsbot.py`.

## Scheduling

WSL/Linux cron example, every market weekday at 8:30 AM:

    crontab -e

Add:

    30 8 * * 1-5 cd /mnt/e/Code/free-market-news && /usr/bin/python3 newsbot.py --portfolio portfolio.json --db news.db --min-score 2 --limit 40 >> news.log 2>&1

Hourly weekday example:

    0 8-17 * * 1-5 cd /mnt/e/Code/free-market-news && /usr/bin/python3 newsbot.py --portfolio portfolio.json --db news.db --min-score 3 --limit 20 >> news.log 2>&1

## Tests

Run:

    cd /mnt/e/Code/free-market-news
    python3 -m unittest discover -s tests -v

Current verified result:

    Ran 7 tests
    OK

## Notes and limitations

- RSS and public web feeds can change or rate-limit.
- Google News can produce duplicates and irrelevant matches, especially for ambiguous tickers like `V`, `NET`, and `SE`.
- Yahoo RSS is ticker-specific, not a full global market-news feed.
- SEC filings are primary-source data, but not article/news summaries.
- This prototype stores by URL, so syndicated Google redirect URLs and direct article URLs may still appear as separate items.

## Obvious next improvements

- Add article clustering by normalized title, not just URL.
- Add company-specific disambiguation rules for short tickers.
- Add HTML report output.
- Add email/desktop notification delivery.
- Add an LLM summarizer layer for morning/close briefings.
- Add market-mover ticker discovery from free pages.
