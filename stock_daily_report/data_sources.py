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
from .models import CompanyProfile, EarningsEvent, FundamentalMetric, FundamentalSnapshot, NewsItem, Quote, Security


YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=5d&interval=1d"
YAHOO_RSS_URL = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US"
NASDAQ_EARNINGS_URL = "https://api.nasdaq.com/api/calendar/earnings"
YAHOO_QUOTE_SUMMARY_URL = "https://query2.finance.yahoo.com/v10/finance/quoteSummary/{symbol}?modules={modules}"


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
        data = payload.get("data") if isinstance(payload, dict) else None
        rows = data.get("rows", []) if isinstance(data, dict) else []
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



def fetch_company_profile(symbol: str, app_config: AppConfig) -> CompanyProfile:
    """Fetch company profile fields suitable for qualitative fundamental analysis."""

    payload, error = _fetch_quote_summary(symbol, app_config, ["assetProfile", "price"])
    if error:
        return CompanyProfile(symbol=symbol, name=symbol, error=error)
    result = _quote_summary_result(payload)
    profile = result.get("assetProfile", {}) if isinstance(result, dict) else {}
    price = result.get("price", {}) if isinstance(result, dict) else {}
    name = _display_value(price.get("longName")) or _display_value(price.get("shortName")) or symbol
    return CompanyProfile(
        symbol=symbol,
        name=name,
        sector=_display_value(profile.get("sector")) or "",
        industry=_display_value(profile.get("industry")) or "",
        website=_display_value(profile.get("website")) or "",
        summary=_display_value(profile.get("longBusinessSummary")) or "",
    )


def fetch_fundamentals(symbol: str, app_config: AppConfig) -> FundamentalSnapshot:
    """Fetch and normalize key fundamentals for Fisher-style growth analysis."""

    modules = ["financialData", "defaultKeyStatistics", "summaryDetail", "price"]
    payload, error = _fetch_quote_summary(symbol, app_config, modules)
    if error:
        return FundamentalSnapshot(symbol=symbol, error=error)
    result = _quote_summary_result(payload)
    if not result:
        return FundamentalSnapshot(symbol=symbol, error="empty quoteSummary result")

    financial = result.get("financialData", {})
    stats = result.get("defaultKeyStatistics", {})
    summary = result.get("summaryDetail", {})
    price = result.get("price", {})
    metrics = [
        _metric("市值", price.get("marketCap"), "规模与融资能力的粗略代理。", "neutral"),
        _metric("收入增长", financial.get("revenueGrowth"), "费雪框架重视长期可扩展市场和销售成长。", _status_from_percent(financial.get("revenueGrowth"), 0.10, 0.0)),
        _metric("毛利率", financial.get("grossMargins"), "高毛利通常意味着产品差异化、定价权或规模优势。", _status_from_percent(financial.get("grossMargins"), 0.40, 0.25)),
        _metric("营业利润率", financial.get("operatingMargins"), "衡量管理层将增长转化为经营利润的能力。", _status_from_percent(financial.get("operatingMargins"), 0.20, 0.10)),
        _metric("净利率", financial.get("profitMargins"), "反映成本控制、商业模式质量和周期韧性。", _status_from_percent(financial.get("profitMargins"), 0.15, 0.05)),
        _metric("ROE", financial.get("returnOnEquity"), "资本效率越高，越符合成长股复利要求。", _status_from_percent(financial.get("returnOnEquity"), 0.15, 0.08)),
        _metric("自由现金流", financial.get("freeCashflow"), "成长投资需要关注利润是否能沉淀为现金。", "positive" if _raw_number(financial.get("freeCashflow")) and _raw_number(financial.get("freeCashflow")) > 0 else "negative"),
        _metric("负债/权益", financial.get("debtToEquity"), "财务杠杆过高会削弱长期投入和逆周期能力。", _status_from_inverse(financial.get("debtToEquity"), 80, 150)),
        _metric("远期 P/E", summary.get("forwardPE"), "估值不是费雪框架核心，但决定安全边际与预期门槛。", "neutral"),
        _metric("PEG", stats.get("pegRatio"), "以增长校准估值，低于 1 通常更有吸引力。", _status_from_inverse(stats.get("pegRatio"), 1.2, 2.0)),
    ]
    return FundamentalSnapshot(symbol=symbol, metrics=[item for item in metrics if item.value != "N/A"])

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



def _fetch_quote_summary(symbol: str, app_config: AppConfig, modules: list[str]) -> tuple[dict | None, str | None]:
    url = YAHOO_QUOTE_SUMMARY_URL.format(symbol=quote_plus(symbol), modules=quote_plus(",".join(modules)))
    try:
        return json.loads(_get_text(url, app_config)), None
    except (ValueError, URLError, HTTPError) as exc:
        return None, str(exc)


def _quote_summary_result(payload: dict | None) -> dict:
    if not isinstance(payload, dict):
        return {}
    result = payload.get("quoteSummary", {}).get("result")
    if isinstance(result, list) and result:
        return result[0] if isinstance(result[0], dict) else {}
    return {}


def _display_value(value: object) -> str | None:
    if isinstance(value, dict):
        if value.get("fmt") is not None:
            return str(value["fmt"])
        if value.get("longFmt") is not None:
            return str(value["longFmt"])
        value = value.get("raw")
    if value is None:
        return None
    return str(value)


def _raw_number(value: object) -> float | None:
    if isinstance(value, dict):
        value = value.get("raw")
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _metric(label: str, value: object, interpretation: str, status: str) -> FundamentalMetric:
    return FundamentalMetric(label=label, value=_display_value(value) or "N/A", interpretation=interpretation, status=status)


def _status_from_percent(value: object, positive: float, negative: float) -> str:
    raw = _raw_number(value)
    if raw is None:
        return "neutral"
    if raw >= positive:
        return "positive"
    if raw < negative:
        return "negative"
    return "neutral"


def _status_from_inverse(value: object, positive_ceiling: float, negative_floor: float) -> str:
    raw = _raw_number(value)
    if raw is None:
        return "neutral"
    if raw <= positive_ceiling:
        return "positive"
    if raw >= negative_floor:
        return "negative"
    return "neutral"

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
