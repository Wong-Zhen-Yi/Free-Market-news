#!/usr/bin/env python3
"""Small local web UI for the free market news prototype."""

from __future__ import annotations

import argparse
import json
import re
import threading
import time
import urllib.error
import xml.etree.ElementTree as ET
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from newsbot import (
    Storage,
    build_feed_jobs,
    build_google_news_url,
    build_yahoo_rss_url,
    classify_importance,
    dedupe_articles,
    fetch_feed_job,
    load_portfolio,
)


class AppState:
    def __init__(self, db: str, portfolio_path: str, min_score: int, include_general: bool):
        self.db = db
        self.portfolio_path = portfolio_path
        self.min_score = min_score
        self.include_general = include_general
        self.lock = threading.Lock()
        self.fetching = False
        self.last_fetch: dict[str, object] = {
            "status": "idle",
            "message": "Ready",
            "fetched": 0,
            "saved": 0,
            "errors": [],
            "started_at": "",
            "finished_at": "",
        }


def run_fetch(state: AppState, max_feeds: int | None = None) -> None:
    with state.lock:
        if state.fetching:
            return
        state.fetching = True
        state.last_fetch = {
            "status": "running",
            "message": "Fetching feeds...",
            "fetched": 0,
            "saved": 0,
            "errors": [],
            "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "finished_at": "",
        }

    fetched = 0
    saved = 0
    errors: list[str] = []

    try:
        storage = Storage(state.db)
        portfolio = load_portfolio(state.portfolio_path)
        jobs = build_feed_jobs(portfolio, include_general=state.include_general)
        if max_feeds:
            jobs = jobs[:max_feeds]

        articles = []
        for index, (url, query) in enumerate(jobs, start=1):
            try:
                feed_articles = fetch_feed_job(url, query)
                articles.extend(feed_articles)
                with state.lock:
                    state.last_fetch["message"] = f"Fetched {index} of {len(jobs)} feeds"
                    state.last_fetch["fetched"] = len(articles)
            except (urllib.error.URLError, TimeoutError, ET.ParseError, OSError) as exc:
                errors.append(f"{query}: {exc}")

        fetched = len(articles)
        for article in dedupe_articles(articles):
            score, tags = classify_importance(article)
            if score < state.min_score:
                continue
            if storage.save_article(article, score=score, tags=tags):
                saved += 1
    finally:
        with state.lock:
            state.fetching = False
            state.last_fetch = {
                "status": "complete" if not errors else "complete_with_errors",
                "message": f"Saved {saved} new articles",
                "fetched": fetched,
                "saved": saved,
                "errors": errors,
                "started_at": state.last_fetch.get("started_at", ""),
                "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }


def resolve_stock_search(term: str, portfolio: dict[str, str] | None = None) -> tuple[str, str, bool]:
    cleaned = re.sub(r"\s+", " ", term.strip())
    if not cleaned:
        raise ValueError("Enter a ticker or company name")

    portfolio = portfolio or {}
    cleaned_upper = cleaned.upper()
    for ticker, company in portfolio.items():
        if cleaned_upper == ticker.upper() or cleaned.lower() == company.lower():
            return ticker.upper(), company, True

    ticker_match = re.fullmatch(r"[A-Za-z][A-Za-z.\-]{0,9}", cleaned)
    if ticker_match:
        ticker = cleaned.upper().replace(".", "-")
        return ticker, ticker, True

    ticker = re.sub(r"[^A-Za-z]", "", cleaned).upper()[:10] or "STOCK"
    return ticker, cleaned, False


def fetch_single_stock(state: AppState, term: str) -> dict[str, object]:
    portfolio = load_portfolio(state.portfolio_path)
    ticker, company, has_ticker_feed = resolve_stock_search(term, portfolio)
    google_query = f"Google:{ticker}" if has_ticker_feed else f"Search:{company}"
    jobs = [(build_google_news_url(f"{company} {ticker} stock"), google_query)]
    if has_ticker_feed:
        jobs.insert(0, (build_yahoo_rss_url(ticker), f"Yahoo:{ticker}"))
    storage = Storage(state.db)
    articles = []
    errors: list[str] = []
    saved = 0

    try:
        for url, query in jobs:
            try:
                articles.extend(fetch_feed_job(url, query))
            except (urllib.error.URLError, TimeoutError, ET.ParseError, OSError) as exc:
                errors.append(f"{query}: {exc}")

        for article in dedupe_articles(articles):
            score, tags = classify_importance(article)
            if score < state.min_score:
                continue
            if storage.save_article(article, score=score, tags=tags):
                saved += 1
    finally:
        storage.close()

    return {
        "ok": True,
        "ticker": ticker,
        "company": company,
        "query": google_query,
        "fetched": len(articles),
        "saved": saved,
        "errors": errors,
    }


def page_html() -> bytes:
    return HTML.encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    state: AppState

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self.send_bytes(page_html(), "text/html; charset=utf-8")
            return
        if parsed.path == "/api/articles":
            self.send_json(self.get_articles(parsed.query))
            return
        if parsed.path == "/api/stats":
            self.send_json(self.get_stats())
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/stock-search":
            self.handle_stock_search()
            return
        if parsed.path != "/api/fetch":
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        with self.state.lock:
            if self.state.fetching:
                self.send_json({"ok": True, "message": "Fetch already running"})
                return

        thread = threading.Thread(target=run_fetch, args=(self.state,), daemon=True)
        thread.start()
        self.send_json({"ok": True, "message": "Fetch started"})

    def handle_stock_search(self) -> None:
        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length).decode("utf-8") if length else "{}"
        try:
            payload = json.loads(body)
            term = str(payload.get("term", ""))
            result = fetch_single_stock(self.state, term)
            self.send_json(result)
        except ValueError as exc:
            self.send_json({"ok": False, "message": str(exc)})

    def get_articles(self, query_string: str) -> dict[str, object]:
        params = parse_qs(query_string)
        limit = int(params.get("limit", ["60"])[0] or "60")
        min_score = int(params.get("min_score", ["0"])[0] or "0")
        query = params.get("query", [""])[0]
        tag = params.get("tag", [""])[0]
        search = params.get("search", [""])[0].strip()

        storage = Storage(self.state.db)
        try:
            articles = storage.list_articles(
                limit=min(max(limit, 1), 200),
                min_score=min_score,
                query=query,
                tag=tag,
                search=search,
            )
        finally:
            storage.close()
        return {"articles": articles}

    def get_stats(self) -> dict[str, object]:
        storage = Storage(self.state.db)
        try:
            stats = storage.stats()
        finally:
            storage.close()
        with self.state.lock:
            fetch = dict(self.state.last_fetch)
            fetching = self.state.fetching
        return {"stats": stats, "fetch": fetch, "fetching": fetching}

    def send_json(self, payload: dict[str, object]) -> None:
        self.send_bytes(json.dumps(payload).encode("utf-8"), "application/json")

    def send_bytes(self, body: bytes, content_type: str) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local web UI for free market news")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--portfolio", default="portfolio.json")
    parser.add_argument("--db", default="news.db")
    parser.add_argument("--min-score", type=int, default=2)
    parser.add_argument("--no-general", action="store_true")
    return parser


def main() -> int:
    args = make_parser().parse_args()
    db_path = str(Path(args.db))
    state = AppState(
        db=db_path,
        portfolio_path=args.portfolio,
        min_score=args.min_score,
        include_general=not args.no_general,
    )

    Handler.state = state
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Free Market News UI running at http://{args.host}:{args.port}")
    print("Press Ctrl+C to stop.")
    server.serve_forever()
    return 0


HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Free Market News</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f6f7f4;
      --ink: #161819;
      --muted: #636a6f;
      --line: #d9ddd7;
      --panel: #ffffff;
      --accent: #007c89;
      --accent-dark: #005d66;
      --warm: #c65328;
      --good: #1d7f4e;
      --shadow: 0 18px 42px rgba(22, 24, 25, 0.08);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--ink);
    }
    button, input, select { font: inherit; }
    .topbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 18px 28px;
      border-bottom: 1px solid var(--line);
      background: rgba(255,255,255,0.82);
      position: sticky;
      top: 0;
      z-index: 4;
      backdrop-filter: blur(14px);
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 12px;
      min-width: 220px;
    }
    .mark {
      width: 36px;
      height: 36px;
      display: grid;
      place-items: center;
      border-radius: 8px;
      background: var(--ink);
      color: white;
      font-weight: 800;
    }
    h1 {
      margin: 0;
      font-size: 18px;
      line-height: 1.1;
      letter-spacing: 0;
    }
    .subtle {
      color: var(--muted);
      font-size: 13px;
      margin-top: 3px;
    }
    .actions { display: flex; align-items: center; gap: 10px; }
    .button {
      border: 1px solid transparent;
      border-radius: 8px;
      min-height: 40px;
      padding: 0 14px;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 8px;
      background: var(--accent);
      color: white;
      font-weight: 700;
    }
    .button:hover { background: var(--accent-dark); }
    .button.secondary {
      background: #fff;
      color: var(--ink);
      border-color: var(--line);
    }
    .button.secondary:hover { background: #edf1ed; }
    .button[disabled] { opacity: .6; cursor: progress; }
    .shell {
      display: grid;
      grid-template-columns: 280px minmax(0, 1fr);
      min-height: calc(100vh - 77px);
    }
    aside {
      border-right: 1px solid var(--line);
      padding: 22px 18px;
      background: #eef1ec;
    }
    main {
      padding: 24px 28px 42px;
    }
    .stats {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 12px;
      margin-bottom: 18px;
    }
    .stat {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
      box-shadow: 0 8px 20px rgba(22, 24, 25, 0.04);
      min-height: 86px;
    }
    .stat .value { font-size: 28px; font-weight: 800; line-height: 1; }
    .stat .label { color: var(--muted); font-size: 12px; margin-top: 8px; }
    .filters {
      display: grid;
      gap: 12px;
    }
    .stock-search {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 8px;
      align-items: end;
      margin-bottom: 16px;
      padding-bottom: 16px;
      border-bottom: 1px solid var(--line);
    }
    .stock-search label { min-width: 0; }
    .stock-search .button {
      min-width: 86px;
      justify-content: center;
      padding: 0 12px;
    }
    label {
      display: grid;
      gap: 7px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
    }
    input, select {
      width: 100%;
      border: 1px solid var(--line);
      background: white;
      min-height: 38px;
      border-radius: 8px;
      padding: 0 10px;
      color: var(--ink);
    }
    .section-title {
      font-size: 12px;
      text-transform: uppercase;
      color: var(--muted);
      font-weight: 800;
      margin: 24px 0 10px;
    }
    .chips {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }
    .chip {
      border: 1px solid var(--line);
      background: white;
      border-radius: 999px;
      padding: 6px 10px;
      font-size: 12px;
      cursor: pointer;
    }
    .chip.active {
      background: var(--ink);
      color: white;
      border-color: var(--ink);
    }
    .feed {
      display: grid;
      gap: 10px;
    }
    .article {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
      display: grid;
      gap: 10px;
      box-shadow: 0 8px 22px rgba(22, 24, 25, 0.045);
    }
    .article:hover { border-color: #aeb7b0; }
    .article-head {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 14px;
    }
    .title {
      color: var(--ink);
      font-weight: 800;
      text-decoration: none;
      line-height: 1.28;
      font-size: 17px;
    }
    .title:hover { color: var(--accent-dark); }
    .score {
      flex: 0 0 auto;
      width: 40px;
      height: 34px;
      display: grid;
      place-items: center;
      border-radius: 8px;
      background: #e4f3ee;
      color: var(--good);
      font-weight: 900;
    }
    .summary {
      color: #3e4447;
      line-height: 1.45;
      margin: 0;
      max-width: 900px;
    }
    .meta {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
      color: var(--muted);
      font-size: 12px;
    }
    .pill {
      border-radius: 999px;
      background: #f0f2ef;
      color: #42484b;
      padding: 5px 8px;
      font-weight: 700;
    }
    .tag { background: #fff2ea; color: var(--warm); }
    .status {
      color: var(--muted);
      font-size: 13px;
      min-height: 20px;
    }
    .search-result {
      color: var(--muted);
      font-size: 12px;
      min-height: 18px;
      margin: -6px 0 14px;
    }
    .empty {
      border: 1px dashed #b8c0b8;
      border-radius: 8px;
      padding: 34px;
      color: var(--muted);
      text-align: center;
      background: rgba(255,255,255,0.5);
    }
    @media (max-width: 840px) {
      .topbar { align-items: flex-start; flex-direction: column; padding: 16px; }
      .shell { grid-template-columns: 1fr; }
      aside { border-right: 0; border-bottom: 1px solid var(--line); }
      main { padding: 18px 16px 32px; }
      .stats { grid-template-columns: 1fr; }
      .actions { width: 100%; }
      .button { flex: 1; justify-content: center; }
    }
  </style>
</head>
<body>
  <header class="topbar">
    <div class="brand">
      <div class="mark">FM</div>
      <div>
        <h1>Free Market News</h1>
        <div class="subtle">Local signal scanner for your watchlist</div>
      </div>
    </div>
    <div class="actions">
      <button id="refresh" class="button secondary" title="Reload saved articles">Refresh</button>
      <button id="fetch" class="button" title="Fetch latest feeds">Fetch latest</button>
    </div>
  </header>
  <div class="shell">
    <aside>
      <div class="stock-search">
        <label>Find Stock
          <input id="stockTerm" type="search" placeholder="NVDA or Nvidia">
        </label>
        <button id="stockSearch" class="button secondary" title="Fetch news for one stock">Search</button>
      </div>
      <div id="stockResult" class="search-result"></div>
      <div class="filters">
        <label>Search
          <input id="search" type="search" placeholder="Ticker, title, source">
        </label>
        <label>Minimum score
          <select id="minScore">
            <option value="0">All saved</option>
            <option value="2" selected>2+</option>
            <option value="3">3+</option>
            <option value="5">5+</option>
          </select>
        </label>
        <label>Watchlist feed
          <select id="query">
            <option value="">All feeds</option>
          </select>
        </label>
      </div>
      <div class="section-title">Signal Tags</div>
      <div id="tags" class="chips"></div>
      <div class="section-title">Status</div>
      <div id="status" class="status">Loading...</div>
    </aside>
    <main>
      <section class="stats">
        <div class="stat"><div id="total" class="value">0</div><div class="label">Saved articles</div></div>
        <div class="stat"><div id="highSignal" class="value">0</div><div class="label">Score 3+</div></div>
        <div class="stat"><div id="newSaved" class="value">0</div><div class="label">New last fetch</div></div>
      </section>
      <section id="feed" class="feed"></section>
    </main>
  </div>
  <script>
    const state = { tag: "", timer: null };
    const els = {
      feed: document.querySelector("#feed"),
      status: document.querySelector("#status"),
      total: document.querySelector("#total"),
      highSignal: document.querySelector("#highSignal"),
      newSaved: document.querySelector("#newSaved"),
      tags: document.querySelector("#tags"),
      query: document.querySelector("#query"),
      minScore: document.querySelector("#minScore"),
      search: document.querySelector("#search"),
      stockTerm: document.querySelector("#stockTerm"),
      stockSearch: document.querySelector("#stockSearch"),
      stockResult: document.querySelector("#stockResult"),
      fetch: document.querySelector("#fetch"),
      refresh: document.querySelector("#refresh")
    };

    function esc(value) {
      return String(value || "").replace(/[&<>"']/g, ch => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;"
      }[ch]));
    }

    function tagsHtml(tags) {
      if (!tags) return '<span class="pill">general</span>';
      return tags.split(",").filter(Boolean).map(tag => `<span class="pill tag">${esc(tag)}</span>`).join("");
    }

    function articleHtml(article) {
      return `<article class="article">
        <div class="article-head">
          <a class="title" href="${esc(article.url)}" target="_blank" rel="noreferrer">${esc(article.title)}</a>
          <div class="score" title="Signal score">${esc(article.score)}</div>
        </div>
        ${article.summary ? `<p class="summary">${esc(article.summary)}</p>` : ""}
        <div class="meta">
          <span class="pill">${esc(article.query)}</span>
          ${tagsHtml(article.tags)}
          <span>${esc(article.published || "No publish date")}</span>
          <span>${esc(article.source)}</span>
        </div>
      </article>`;
    }

    async function loadStats() {
      const response = await fetch("/api/stats");
      const data = await response.json();
      els.total.textContent = data.stats.total;
      els.highSignal.textContent = data.stats.high_signal;
      els.newSaved.textContent = data.fetch.saved || 0;
      els.fetch.disabled = data.fetching;
      els.status.textContent = data.fetch.message || "Ready";
      renderTags(data.stats.tags || []);
      renderQueries(data.stats.queries || []);
      if (data.fetching && !state.timer) {
        state.timer = setInterval(refreshAll, 1800);
      }
      if (!data.fetching && state.timer) {
        clearInterval(state.timer);
        state.timer = null;
      }
    }

    function renderTags(tags) {
      const all = [{ name: "", count: "All" }].concat(tags.map(([name, count]) => ({ name, count })));
      els.tags.innerHTML = all.map(tag => `<button class="chip ${state.tag === tag.name ? "active" : ""}" data-tag="${esc(tag.name)}">${esc(tag.name || "All")} ${esc(tag.count)}</button>`).join("");
      els.tags.querySelectorAll("button").forEach(button => {
        button.addEventListener("click", () => {
          state.tag = button.dataset.tag;
          loadArticles();
          renderTags(tags);
        });
      });
    }

    function renderQueries(queries) {
      const current = els.query.value;
      els.query.innerHTML = '<option value="">All feeds</option>' + queries.map(item => `<option value="${esc(item.query)}">${esc(item.query)} (${esc(item.count)})</option>`).join("");
      els.query.value = current;
    }

    async function loadArticles() {
      const params = new URLSearchParams({
        limit: "80",
        min_score: els.minScore.value,
        query: els.query.value,
        tag: state.tag,
        search: els.search.value
      });
      const response = await fetch(`/api/articles?${params}`);
      const data = await response.json();
      els.feed.innerHTML = data.articles.length
        ? data.articles.map(articleHtml).join("")
        : '<div class="empty">No saved articles match the current filters.</div>';
    }

    async function refreshAll() {
      await loadStats();
      await loadArticles();
    }

    async function startFetch() {
      els.fetch.disabled = true;
      els.status.textContent = "Starting fetch...";
      await fetch("/api/fetch", { method: "POST" });
      await refreshAll();
    }

    async function searchStock() {
      const term = els.stockTerm.value.trim();
      if (!term) {
        els.stockResult.textContent = "Enter a ticker or company name.";
        return;
      }
      els.stockSearch.disabled = true;
      els.stockResult.textContent = `Searching ${term}...`;
      try {
        const response = await fetch("/api/stock-search", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ term })
        });
        const data = await response.json();
        if (!data.ok) {
          els.stockResult.textContent = data.message || "Search failed.";
          return;
        }
        await loadStats();
        els.query.value = data.query;
        els.search.value = "";
        state.tag = "";
        await loadArticles();
        els.stockResult.textContent = `${data.ticker}: fetched ${data.fetched}, saved ${data.saved} new.`;
      } catch (error) {
        els.stockResult.textContent = "Search failed. Check the server log in data.";
      } finally {
        els.stockSearch.disabled = false;
      }
    }

    els.fetch.addEventListener("click", startFetch);
    els.stockSearch.addEventListener("click", searchStock);
    els.stockTerm.addEventListener("keydown", event => {
      if (event.key === "Enter") searchStock();
    });
    els.refresh.addEventListener("click", refreshAll);
    els.minScore.addEventListener("change", loadArticles);
    els.query.addEventListener("change", loadArticles);
    els.search.addEventListener("input", () => {
      clearTimeout(state.searchTimer);
      state.searchTimer = setTimeout(loadArticles, 180);
    });
    refreshAll();
  </script>
</body>
</html>
"""


if __name__ == "__main__":
    raise SystemExit(main())
