#!/usr/bin/env python3
"""Free stock-market news fetcher prototype.

Sources used by default:
- Yahoo Finance RSS per ticker
- Google News RSS per ticker/company and broad market topic
- Optional SEC EDGAR recent filings

No paid APIs and no third-party Python packages are required.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

DEFAULT_PORTFOLIO = {
    "PLTR": "Palantir",
    "SHOP": "Shopify",
    "RBRK": "Rubrik",
    "NET": "Cloudflare",
    "V": "Visa",
    "RBLX": "Roblox",
    "PANW": "Palo Alto Networks",
    "MSTR": "MicroStrategy",
    "SE": "Sea Limited",
    "SOFI": "SoFi",
    "ZS": "Zscaler",
    "ORCL": "Oracle",
    "HOOD": "Robinhood",
    "MSFT": "Microsoft",
    "NVDA": "Nvidia",
}

DEFAULT_TOPICS = [
    "stock market today",
    "Federal Reserve stocks",
    "Treasury yields stocks",
    "AI stocks",
    "semiconductor stocks",
    "cybersecurity stocks",
    "crypto stocks",
]

GENERAL_FEEDS = [
    "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "https://feeds.marketwatch.com/marketwatch/topstories/",
    "https://finance.yahoo.com/news/rssindex",
]

HIGH_SIGNAL_KEYWORDS = {
    "earnings": ["earnings", "eps", "quarter", "q1", "q2", "q3", "q4", "results"],
    "guidance": ["guidance", "forecast", "outlook", "raises forecast", "cuts forecast"],
    "analyst": ["upgrade", "downgrade", "price target", "analyst", "initiates"],
    "filing": ["8-k", "10-q", "10-k", "sec", "form 4", "insider"],
    "deal": ["acquisition", "merger", "buyout", "partnership", "contract"],
    "risk": ["lawsuit", "investigation", "probe", "regulation", "offering", "dilution", "debt"],
    "capital_return": ["buyback", "repurchase", "dividend"],
}

LOW_SIGNAL_PATTERNS = [
    "should you buy",
    "millionaire-maker",
    "no-brainer",
    "could soar",
    "could crash",
    "3 stocks",
    "zacks rank",
]


@dataclass(frozen=True)
class Article:
    title: str
    url: str
    source: str
    query: str
    published: str = ""
    summary: str = ""


def build_yahoo_rss_url(ticker: str) -> str:
    symbol = ticker.strip().upper()
    return f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US"


def build_google_news_url(query: str) -> str:
    params = urllib.parse.urlencode(
        {"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"},
        quote_via=urllib.parse.quote,
    )
    return f"https://news.google.com/rss/search?{params}"


def fetch_text(url: str, timeout: int = 20) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "free-market-news-prototype/0.1 (+local personal news monitor)",
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def _child_text(element: ET.Element, tag: str) -> str:
    child = element.find(tag)
    if child is None or child.text is None:
        return ""
    return html.unescape(child.text.strip())


def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def parse_rss(xml_text: str, source: str, query: str) -> list[Article]:
    root = ET.fromstring(xml_text)
    articles: list[Article] = []

    for item in root.findall(".//item"):
        title = _child_text(item, "title")
        link = _child_text(item, "link")
        published = _child_text(item, "pubDate") or _child_text(item, "published")
        summary = _strip_html(_child_text(item, "description"))

        if not link:
            continue

        articles.append(
            Article(
                title=title or "(untitled)",
                url=link,
                source=source,
                query=query,
                published=published,
                summary=summary,
            )
        )

    return articles


def dedupe_articles(articles: Iterable[Article]) -> list[Article]:
    seen: set[str] = set()
    deduped: list[Article] = []
    for article in articles:
        key = article.url.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(article)
    return deduped


def classify_importance(article: Article) -> tuple[int, list[str]]:
    text = f"{article.title} {article.summary}".lower()
    score = 0
    tags: list[str] = []

    for tag, keywords in HIGH_SIGNAL_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            score += 2
            tags.append(tag)

    if any(pattern in text for pattern in LOW_SIGNAL_PATTERNS):
        score -= 2
        tags.append("low_signal")

    if len(tags) >= 2 and "low_signal" not in tags:
        score += 1

    if article.query.startswith("Yahoo:") or article.query.startswith("Google:"):
        score += 1

    return max(score, 0), tags


class Storage:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self.conn = sqlite3.connect(self.path)
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS articles (
                url TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                source TEXT NOT NULL,
                query TEXT NOT NULL,
                published TEXT,
                summary TEXT,
                score INTEGER NOT NULL DEFAULT 0,
                tags TEXT NOT NULL DEFAULT '',
                fetched_at TEXT NOT NULL
            )
            """
        )
        self.conn.commit()

    def save_article(self, article: Article, score: int, tags: list[str]) -> bool:
        try:
            self.conn.execute(
                """
                INSERT INTO articles
                (url, title, source, query, published, summary, score, tags, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    article.url,
                    article.title,
                    article.source,
                    article.query,
                    article.published,
                    article.summary,
                    score,
                    ",".join(tags),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def close(self) -> None:
        self.conn.close()

    def recent(self, limit: int = 20) -> list[tuple]:
        return self.conn.execute(
            """
            SELECT title, url, source, query, score, tags, published
            FROM articles
            ORDER BY fetched_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    def list_articles(
        self,
        limit: int = 50,
        min_score: int = 0,
        query: str = "",
        tag: str = "",
        search: str = "",
    ) -> list[dict[str, str | int]]:
        sql = """
            SELECT title, url, source, query, score, tags, published, summary, fetched_at
            FROM articles
            WHERE score >= ?
        """
        params: list[str | int] = [min_score]

        if query:
            sql += " AND query = ?"
            params.append(query)
        if tag:
            sql += " AND (',' || tags || ',') LIKE ?"
            params.append(f"%,{tag},%")
        if search:
            sql += " AND (title LIKE ? OR summary LIKE ? OR source LIKE ? OR query LIKE ?)"
            pattern = f"%{search}%"
            params.extend([pattern, pattern, pattern, pattern])

        sql += " ORDER BY fetched_at DESC LIMIT ?"
        params.append(limit)

        rows = self.conn.execute(sql, params).fetchall()
        return [
            {
                "title": row[0],
                "url": row[1],
                "source": row[2],
                "query": row[3],
                "score": row[4],
                "tags": row[5],
                "published": row[6],
                "summary": row[7],
                "fetched_at": row[8],
            }
            for row in rows
        ]

    def stats(self) -> dict[str, object]:
        total, high_signal, latest = self.conn.execute(
            """
            SELECT
                COUNT(*),
                SUM(CASE WHEN score >= 3 THEN 1 ELSE 0 END),
                MAX(fetched_at)
            FROM articles
            """
        ).fetchone()
        tag_rows = self.conn.execute(
            """
            SELECT tags
            FROM articles
            WHERE tags != ''
            ORDER BY fetched_at DESC
            LIMIT 500
            """
        ).fetchall()
        query_rows = self.conn.execute(
            """
            SELECT query, COUNT(*) AS count
            FROM articles
            GROUP BY query
            ORDER BY count DESC, query ASC
            LIMIT 30
            """
        ).fetchall()

        tag_counts: dict[str, int] = {}
        for (tag_text,) in tag_rows:
            for tag in tag_text.split(","):
                if tag:
                    tag_counts[tag] = tag_counts.get(tag, 0) + 1

        return {
            "total": total or 0,
            "high_signal": high_signal or 0,
            "latest": latest or "",
            "tags": sorted(tag_counts.items(), key=lambda item: (-item[1], item[0])),
            "queries": [{"query": row[0], "count": row[1]} for row in query_rows],
        }


def load_portfolio(path: str | None) -> dict[str, str]:
    if not path:
        return dict(DEFAULT_PORTFOLIO)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        return {str(k).upper(): str(v) for k, v in data.items()}
    if isinstance(data, list):
        return {str(t).upper(): str(t).upper() for t in data}
    raise ValueError("Portfolio JSON must be an object of ticker->company or a list of tickers")


def build_feed_jobs(portfolio: dict[str, str], include_general: bool = True) -> list[tuple[str, str]]:
    jobs: list[tuple[str, str]] = []
    for ticker, company in portfolio.items():
        jobs.append((build_yahoo_rss_url(ticker), f"Yahoo:{ticker}"))
        jobs.append((build_google_news_url(f"{company} {ticker} stock"), f"Google:{ticker}"))
    for topic in DEFAULT_TOPICS:
        jobs.append((build_google_news_url(topic), f"GoogleTopic:{topic}"))
    if include_general:
        for url in GENERAL_FEEDS:
            jobs.append((url, "GeneralMarket"))
    return jobs


def fetch_feed_job(url: str, query: str) -> list[Article]:
    xml_text = fetch_text(url)
    source = query
    try:
        root = ET.fromstring(xml_text)
        feed_title = root.findtext(".//channel/title")
        if feed_title:
            source = html.unescape(feed_title.strip())
    except ET.ParseError:
        pass
    return parse_rss(xml_text, source=source, query=query)


def fetch_sec_recent_forms(tickers: Iterable[str], user_agent: str, forms: set[str] | None = None) -> list[Article]:
    """Fetch recent SEC filing notices as Article records.

    SEC requires a descriptive User-Agent with contact info. Use --sec-user-agent.
    """
    forms = forms or {"8-K", "10-Q", "10-K", "4"}
    headers = {"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"}

    def get_json(url: str) -> dict:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))

    mapping = get_json("https://www.sec.gov/files/company_tickers.json")
    ticker_to_cik = {item["ticker"].upper(): str(item["cik_str"]).zfill(10) for item in mapping.values()}
    articles: list[Article] = []

    for ticker in tickers:
        cik = ticker_to_cik.get(ticker.upper())
        if not cik:
            continue
        data = get_json(f"https://data.sec.gov/submissions/CIK{cik}.json")
        recent = data.get("filings", {}).get("recent", {})
        company = data.get("name", ticker)
        for form, date, accession, doc in zip(
            recent.get("form", [])[:20],
            recent.get("filingDate", [])[:20],
            recent.get("accessionNumber", [])[:20],
            recent.get("primaryDocument", [])[:20],
        ):
            if form not in forms:
                continue
            accession_clean = accession.replace("-", "")
            url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_clean}/{doc}"
            articles.append(
                Article(
                    title=f"{ticker}: {company} filed {form} on {date}",
                    url=url,
                    source="SEC EDGAR",
                    query=f"SEC:{ticker}",
                    published=date,
                    summary=f"Official SEC filing {form}; accession {accession}.",
                )
            )
        time.sleep(0.12)
    return articles


def run(args: argparse.Namespace) -> int:
    portfolio = load_portfolio(args.portfolio)
    storage = Storage(args.db)

    all_articles: list[Article] = []
    errors: list[str] = []
    jobs = build_feed_jobs(portfolio, include_general=not args.no_general)

    if args.max_feeds:
        jobs = jobs[: args.max_feeds]

    for index, (url, query) in enumerate(jobs, start=1):
        try:
            articles = fetch_feed_job(url, query)
            all_articles.extend(articles)
            if args.verbose:
                print(f"Fetched {len(articles):3d} from {query}")
        except (urllib.error.URLError, TimeoutError, ET.ParseError, OSError) as exc:
            errors.append(f"{query}: {exc}")
            if args.verbose:
                print(f"ERROR {query}: {exc}", file=sys.stderr)
        if args.sleep and index < len(jobs):
            time.sleep(args.sleep)

    if args.sec_user_agent:
        try:
            sec_articles = fetch_sec_recent_forms(portfolio.keys(), args.sec_user_agent)
            all_articles.extend(sec_articles)
            if args.verbose:
                print(f"Fetched {len(sec_articles):3d} SEC filing notices")
        except Exception as exc:  # SEC monitoring is optional in this prototype.
            errors.append(f"SEC: {exc}")
            if args.verbose:
                print(f"ERROR SEC: {exc}", file=sys.stderr)

    new_items: list[tuple[Article, int, list[str]]] = []
    for article in dedupe_articles(all_articles):
        score, tags = classify_importance(article)
        if score < args.min_score:
            continue
        if storage.save_article(article, score=score, tags=tags):
            new_items.append((article, score, tags))

    new_items.sort(key=lambda item: item[1], reverse=True)

    print(f"Fetched articles: {len(all_articles)}")
    print(f"New saved articles: {len(new_items)}")
    if errors:
        print(f"Fetch errors: {len(errors)}")

    for article, score, tags in new_items[: args.limit]:
        tag_text = ",".join(tags) if tags else "general"
        print()
        print(f"[{score} | {tag_text} | {article.query}] {article.title}")
        if article.published:
            print(f"Published: {article.published}")
        print(article.url)

    if args.show_errors and errors:
        print("\nErrors:")
        for error in errors:
            print(f"- {error}")

    return 0


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Free stock-market news fetcher prototype")
    parser.add_argument("--portfolio", help="JSON file: ticker->company object or ticker list")
    parser.add_argument("--db", default="news.db", help="SQLite database path; default: news.db")
    parser.add_argument("--limit", type=int, default=30, help="Max new articles to print")
    parser.add_argument("--min-score", type=int, default=0, help="Only save/print articles at or above this score")
    parser.add_argument("--sleep", type=float, default=0.2, help="Delay between feed fetches")
    parser.add_argument("--max-feeds", type=int, help="Limit feed count for testing/demo")
    parser.add_argument("--no-general", action="store_true", help="Skip broad general market RSS feeds")
    parser.add_argument("--sec-user-agent", help="Enable SEC filing fetches; provide 'Name email@example.com'")
    parser.add_argument("--verbose", action="store_true", help="Print per-feed progress")
    parser.add_argument("--show-errors", action="store_true", help="Print fetch errors")
    return parser


if __name__ == "__main__":
    raise SystemExit(run(make_parser().parse_args()))
