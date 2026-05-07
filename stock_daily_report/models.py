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


@dataclass(frozen=True)
class FundamentalMetric:
    label: str
    value: str
    interpretation: str
    status: str = "neutral"


@dataclass(frozen=True)
class CompanyProfile:
    symbol: str
    name: str
    sector: str = ""
    industry: str = ""
    website: str = ""
    summary: str = ""
    source: str = "Yahoo Finance quoteSummary"
    error: str | None = None


@dataclass(frozen=True)
class FundamentalSnapshot:
    symbol: str
    metrics: list[FundamentalMetric] = field(default_factory=list)
    source: str = "Yahoo Finance quoteSummary"
    error: str | None = None


@dataclass(frozen=True)
class SecFiling:
    form: str
    filing_date: str
    report_date: str
    accession_number: str
    primary_document: str
    description: str = ""
    url: str = ""


@dataclass(frozen=True)
class SecFactPoint:
    label: str
    tag: str
    fiscal_period: str
    fiscal_year: int | None
    end_date: str
    filed_date: str
    form: str
    value: float
    unit: str


@dataclass(frozen=True)
class SecFundamentalData:
    symbol: str
    cik: str | None = None
    filings: list[SecFiling] = field(default_factory=list)
    facts: dict[str, list[SecFactPoint]] = field(default_factory=dict)
    source: str = "SEC EDGAR submissions/companyfacts"
    error: str | None = None


@dataclass(frozen=True)
class FisherCriterion:
    number: int
    title: str
    question: str
    assessment: str
    evidence: list[str] = field(default_factory=list)
    score: int | None = None


@dataclass(frozen=True)
class AnnualReportFile:
    filename: str
    path: str
    file_type: str
    status: str
    content: str = ""
    error: str | None = None


@dataclass(frozen=True)
class AnnualReportEvidenceItem:
    keyword: str
    filename: str
    excerpt: str


@dataclass(frozen=True)
class AnnualReportEvidence:
    report_dir: str
    files: list[AnnualReportFile] = field(default_factory=list)
    evidence: list[AnnualReportEvidenceItem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class FisherAnalysis:
    generated_at: datetime
    security: Security
    quote: Quote
    profile: CompanyProfile
    fundamentals: FundamentalSnapshot
    news: list[NewsItem] = field(default_factory=list)
    earnings: EarningsEvent | None = None
    criteria: list[FisherCriterion] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    sec_data: SecFundamentalData = field(default_factory=lambda: SecFundamentalData(symbol=""))
    annual_report_evidence: AnnualReportEvidence = field(default_factory=lambda: AnnualReportEvidence(report_dir=""))


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
