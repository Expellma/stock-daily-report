"""Network data adapters for market data, news, and earnings events.

The adapters intentionally use public, keyless endpoints so the workflow can run
locally without secret management. Each function returns structured errors
instead of raising on transient network failures, allowing the poster job to
finish and leave an auditable JSON artifact.
"""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
from pathlib import Path
import re
from urllib.error import URLError, HTTPError
from urllib.parse import quote_plus, urlencode
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

from .config import AppConfig
from .models import EarningsEvent, NewsItem, Quote, Security


YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=5d&interval=1d"
YAHOO_RSS_URL = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US"
NASDAQ_EARNINGS_URL = "https://api.nasdaq.com/api/calendar/earnings"


def read_securities(path: Path, include_thesis: bool = True) -> list[Security]:
    """Read a symbol CSV into normalized securities."""

    with path.open(newline="", encoding="utf-8") as fh:
        rows = csv.DictReader(fh)
        securities: list[Security] = []
        for row in rows:
            symbol = row.get("symbol", "").strip().upper()
            if not symbol:
                continue
            securities.append(
                Security(
                    symbol=symbol,
                    name=row.get("name", symbol).strip() or symbol,
                    thesis=(row.get("thesis", "").strip() if include_thesis else ""),
                )
            )
    return securities


def _get_text(url: str, app_config: AppConfig) -> str:
    request = Request(url, headers={"User-Agent": app_config.user_agent})
    with urlopen(request, timeout=app_config.request_timeout_seconds) as response:  # noqa: S310 - configured public URLs only
        return response.read().decode("utf-8", errors="replace")


def fetch_quote(symbol: str, app_config: AppConfig) -> Quote:
    """Fetch latest daily quote data from Yahoo's chart endpoint."""

    try:
        raw = _get_text(YAHOO_CHART_URL.format(symbol=quote_plus(symbol)), app_config)
        payload = json.loads(raw)
        result = payload["chart"]["result"][0]
        meta = result["meta"]
        price = _to_float(meta.get("regularMarketPrice"))
        previous_close = _to_float(meta.get("previousClose"))
        change_percent = None
        if price is not None and previous_close not in (None, 0):
            change_percent = (price - previous_close) / previous_close * 100
        volume = _last_number(result.get("indicators", {}).get("quote", [{}])[0].get("volume", []))
        return Quote(symbol=symbol, price=price, previous_close=previous_close, change_percent=change_percent, volume=volume, source="Yahoo Finance chart")
    except (KeyError, IndexError, ValueError, TypeError, URLError, HTTPError) as exc:
        return Quote(symbol=symbol, source="Yahoo Finance chart", error=str(exc))


def fetch_news(symbol: str, app_config: AppConfig, keywords: list[str], limit: int) -> list[NewsItem]:
    """Fetch and score symbol news from Yahoo Finance RSS."""

    try:
        raw = _get_text(YAHOO_RSS_URL.format(symbol=quote_plus(symbol)), app_config)
        root = ET.fromstring(raw)
    except (ET.ParseError, URLError, HTTPError) as exc:
        return [NewsItem(symbol=symbol, title=f"News fetch failed: {exc}", link="", score=-1)]

    items: list[NewsItem] = []
    for item in root.findall("./channel/item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        published = _parse_rss_date(item.findtext("pubDate"))
        score = score_news(title, keywords)
        if title:
            items.append(NewsItem(symbol=symbol, title=title, link=link, published_at=published, score=score))
    return sorted(items, key=lambda item: (item.score, item.published_at or datetime.min.replace(tzinfo=timezone.utc)), reverse=True)[:limit]


def fetch_earnings(symbol: str, app_config: AppConfig) -> EarningsEvent:
    """Fetch the nearest earnings event from Nasdaq's public calendar API."""

    query = urlencode({"date": datetime.now(timezone.utc).date().isoformat(), "symbol": symbol})
    try:
        raw = _get_text(f"{NASDAQ_EARNINGS_URL}?{query}", app_config)
        payload = json.loads(raw)
        rows = payload.get("data", {}).get("rows", []) or []
        if not rows:
            return EarningsEvent(symbol=symbol)
        row = rows[0]
        return EarningsEvent(
            symbol=symbol,
            report_date=_strip_html(row.get("reportDate") or row.get("date")),
            fiscal_quarter=_strip_html(row.get("fiscalQuarterEnding")),
            estimate=_strip_html(row.get("epsForecast")),
        )
    except (ValueError, TypeError, URLError, HTTPError) as exc:
        return EarningsEvent(symbol=symbol, error=str(exc))


def score_news(title: str, keywords: list[str]) -> int:
    """Score higher-quality, market-moving headlines above generic market wrap."""

    lowered = title.lower()
    score = 0
    for keyword in keywords:
        if keyword.lower() in lowered:
            score += 3
    if re.search(r"\b(q[1-4]|earnings|guidance|revenue|eps)\b", lowered):
        score += 4
    if re.search(r"\b(upgrade|downgrade|raises|cuts|beats|misses)\b", lowered):
        score += 2
    return score


def _to_float(value: object) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _last_number(values: list[object]) -> int | None:
    for value in reversed(values):
        if isinstance(value, (int, float)):
            return int(value)
    return None


def _parse_rss_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _strip_html(value: object) -> str | None:
    if value is None:
        return None
    return re.sub(r"<[^>]+>", "", str(value)).strip() or None
