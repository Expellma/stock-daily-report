from datetime import datetime, timezone

from stock_daily_report.fisher import render_fisher_markdown
from stock_daily_report.models import (
    CompanyProfile,
    EarningsEvent,
    FisherAnalysis,
    FisherCriterion,
    FundamentalMetric,
    FundamentalSnapshot,
    NewsItem,
    Quote,
    SecFactPoint,
    SecFiling,
    SecFundamentalData,
    Security,
)


def test_render_fisher_markdown_includes_framework_sections():
    analysis = FisherAnalysis(
        generated_at=datetime(2026, 5, 6, tzinfo=timezone.utc),
        security=Security("NVDA", "NVIDIA", "AI accelerator demand"),
        quote=Quote("NVDA", price=100.0, change_percent=1.2),
        profile=CompanyProfile("NVDA", "NVIDIA", sector="Technology", industry="Semiconductors", summary="AI chips and platforms."),
        fundamentals=FundamentalSnapshot(
            "NVDA",
            metrics=[FundamentalMetric("收入增长", "20%", "Growth test", "positive"), FundamentalMetric("毛利率", "70%", "Margin test", "positive")],
        ),
        news=[NewsItem("NVDA", "NVIDIA launches new AI platform", "https://example.com", published_at=datetime(2026, 5, 1, tzinfo=timezone.utc))],
        earnings=EarningsEvent("NVDA", report_date="2026-05-20"),
        criteria=[FisherCriterion(1, "产品/服务是否拥有足够大的长期市场空间？", "question", "偏正面", ["evidence"], 4)],
        sec_data=SecFundamentalData(
            "NVDA",
            cik="0001045810",
            filings=[
                SecFiling(
                    "10-Q",
                    "2026-05-01",
                    "2026-04-26",
                    "0001045810-26-000010",
                    "nvda-20260426.htm",
                    "Quarterly report",
                    "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000010/nvda-20260426.htm",
                )
            ],
            facts={
                "收入": [
                    SecFactPoint("收入", "Revenues", "Q1", 2027, "2026-04-26", "2026-05-01", "10-Q", 26000000000, "USD"),
                    SecFactPoint("收入", "Revenues", "Q4", 2026, "2026-01-25", "2026-02-20", "10-K", 22000000000, "USD"),
                ],
                "稀释 EPS": [SecFactPoint("稀释 EPS", "EarningsPerShareDiluted", "Q1", 2027, "2026-04-26", "2026-05-01", "10-Q", 5.12, "USD/shares")],
            },
        ),
    )

    markdown = render_fisher_markdown(analysis)

    assert "# NVIDIA（NVDA）费雪成长投资基本面分析" in markdown
    assert "## 🏛️ SEC EDGAR 近一年财报数据" in markdown
    assert "## 🐟 费雪 15 问逐项检查" in markdown
    assert "| 指标 | 数值 | 状态 | 解读 |" in markdown
    assert "💰 ↗️" in markdown
    assert "打开 SEC 文件" in markdown
    assert "NVIDIA launches new AI platform" in markdown
