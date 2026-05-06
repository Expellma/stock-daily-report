"""Report orchestration and serialization."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path

from .config import Settings
from .data_sources import fetch_earnings, fetch_news, fetch_quote, read_securities
from .models import DailyReport, NewsItem, SecurityDigest


def build_report(settings: Settings) -> DailyReport:
    """Build the complete daily report from configured watchlists."""

    watchlist = read_securities(settings.app.watchlist_path, include_thesis=True)
    sp500 = read_securities(settings.app.sp500_path, include_thesis=False)
    errors: list[str] = []
    digests: list[SecurityDigest] = []

    for security in watchlist:
        quote = fetch_quote(security.symbol, settings.app)
        if quote.error:
            errors.append(f"{security.symbol} quote: {quote.error}")
        earnings = fetch_earnings(security.symbol, settings.app)
        if earnings.error:
            errors.append(f"{security.symbol} earnings: {earnings.error}")
        news = fetch_news(security.symbol, settings.app, settings.signals.major_keywords, settings.app.max_watchlist_news)
        errors.extend(_news_errors(news))
        digests.append(SecurityDigest(security=security, quote=quote, news=[item for item in news if item.score >= 0], earnings=earnings))

    sp500_news = collect_sp500_news(sp500, settings)
    errors.extend(_news_errors(sp500_news))
    sp500_news = [item for item in sp500_news if item.score >= 0]

    return DailyReport(generated_at=datetime.now(timezone.utc), watchlist=digests, sp500_news=sp500_news[: settings.app.max_sp500_news], errors=errors)


def collect_sp500_news(securities, settings: Settings) -> list[NewsItem]:
    """Collect market-moving headlines for the configured S&P 500 universe."""

    all_news: list[NewsItem] = []
    per_symbol_limit = max(2, min(5, settings.app.max_sp500_news // 2))
    for security in securities:
        all_news.extend(fetch_news(security.symbol, settings.app, settings.signals.major_keywords, per_symbol_limit))
    return sorted(all_news, key=lambda item: (item.score, item.published_at or datetime.min.replace(tzinfo=timezone.utc)), reverse=True)


def write_report_json(report: DailyReport, output_dir: Path) -> Path:
    """Persist a structured report artifact for audit and downstream channels."""

    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "daily_report.json"
    path.write_text(json.dumps(_jsonable(asdict(report)), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def output_dir_for(settings: Settings, generated_at: datetime) -> Path:
    return settings.app.output_dir / generated_at.date().isoformat()


def _jsonable(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return value


def _news_errors(news: list[NewsItem]) -> list[str]:
    return [f"{item.symbol} news: {item.title}" for item in news if item.score < 0]
