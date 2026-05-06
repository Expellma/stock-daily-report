"""Shared data structures used across the report pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class Security:
    symbol: str
    name: str
    thesis: str = ""


@dataclass(frozen=True)
class Quote:
    symbol: str
    price: float | None = None
    change_percent: float | None = None
    previous_close: float | None = None
    volume: int | None = None
    source: str = ""
    error: str | None = None


@dataclass(frozen=True)
class NewsItem:
    symbol: str
    title: str
    link: str
    publisher: str = "Yahoo Finance RSS"
    published_at: datetime | None = None
    score: int = 0


@dataclass(frozen=True)
class EarningsEvent:
    symbol: str
    report_date: str | None = None
    fiscal_quarter: str | None = None
    estimate: str | None = None
    source: str = "Nasdaq earnings calendar"
    error: str | None = None


@dataclass
class SecurityDigest:
    security: Security
    quote: Quote
    news: list[NewsItem] = field(default_factory=list)
    earnings: EarningsEvent | None = None


@dataclass
class DailyReport:
    generated_at: datetime
    watchlist: list[SecurityDigest]
    sp500_news: list[NewsItem]
    errors: list[str] = field(default_factory=list)
