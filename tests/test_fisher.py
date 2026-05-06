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
    )

    markdown = render_fisher_markdown(analysis)

    assert "# NVIDIA（NVDA）费雪成长投资基本面分析" in markdown
    assert "## 🐟 费雪 15 问逐项检查" in markdown
    assert "| 指标 | 数值 | 状态 | 解读 |" in markdown
    assert "NVIDIA launches new AI platform" in markdown
