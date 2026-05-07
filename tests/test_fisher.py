from datetime import datetime, timezone

from stock_daily_report.config import Settings
from stock_daily_report.fisher import build_fisher_analysis, render_fisher_markdown
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
            source="SEC EDGAR submissions/companyfacts",
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
    assert "打开披露文件" in markdown
    assert "NVIDIA launches new AI platform" in markdown



def test_build_fisher_analysis_uses_mocked_cn_public_payloads_for_a_share(monkeypatch):
    def fail_overseas(*args, **kwargs):
        raise AssertionError("A-share analysis must not call Yahoo/Nasdaq/SEC adapters")

    monkeypatch.setattr("stock_daily_report.fisher.fetch_quote", fail_overseas)
    monkeypatch.setattr("stock_daily_report.fisher.fetch_company_profile", fail_overseas)
    monkeypatch.setattr("stock_daily_report.fisher.fetch_fundamentals", fail_overseas)
    monkeypatch.setattr("stock_daily_report.fisher.fetch_earnings", fail_overseas)
    monkeypatch.setattr("stock_daily_report.fisher.fetch_sec_fundamentals", fail_overseas)
    monkeypatch.setattr("stock_daily_report.fisher.fetch_news", fail_overseas)

    def fake_get_text(url, app_config):
        if "push2.eastmoney.com" in url:
            return '{"data":{"f43":1025,"f60":1010,"f170":149,"f47":123456,"f58":"浦发银行","f57":"600000"}}'
        if "CompanySurvey" in url:
            return '{"jbzl":[{"SECURITY_NAME_ABBR":"浦发银行","INDUSTRYCSRC1":"金融","INDUSTRYCSRC2":"银行","WWW_ADDRESS":"https://www.spdb.com.cn","BUSINESS_SCOPE":"全国性股份制商业银行。"}]}'
        if "NewFinanceAnalysis" in url:
            return '{"data":[{"TOTAL_OPERATE_INCOME":112000000,"GROSS_PROFIT_RATIO":45.5,"OPERATE_PROFIT_RATIO":28.0,"NETPROFITRATIO":22.0,"ROE":13.0,"DEBT_EQUITY_RATIO":70.0},{"TOTAL_OPERATE_INCOME":100000000,"GROSS_PROFIT_RATIO":40.0,"OPERATE_PROFIT_RATIO":25.0,"NETPROFITRATIO":20.0,"ROE":11.0,"DEBT_EQUITY_RATIO":72.0}]}'
        if "search-api-web.eastmoney.com" in url:
            return 'callback({"data":{"items":[{"title":"浦发银行发布金融科技平台升级","url":"https://example.cn/news","date":"2026-05-01 09:30:00"}]}})'
        if "hisAnnouncement" in url:
            return '{"announcements":[{"announcementTitle":"2025年年度报告","announcementTime":"2026-04-01","announcementId":"cn-1","adjunctUrl":"new/disclosure/detail.pdf"}]}'
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr("stock_daily_report.data_sources._get_text", fake_get_text)

    analysis = build_fisher_analysis(Settings(), "600000.SH", thesis="银行数字化经营")
    markdown = render_fisher_markdown(analysis)

    assert analysis.security.symbol == "600000.SH"
    assert analysis.earnings is None
    assert analysis.quote.price == 10.25
    assert "东方财富/腾讯财经公开行情" in markdown
    assert "东方财富 F10 公司资料" in markdown
    assert "巨潮资讯 CNINFO/交易所公告索引" in markdown
    assert "## 🏛️ 大陆公告/年报索引" in markdown
    assert "浦发银行发布金融科技平台升级" in markdown
    assert "Yahoo Finance quoteSummary" not in markdown
    assert "SEC EDGAR submissions/companyfacts" not in markdown


def test_build_fisher_analysis_keeps_a_share_off_overseas_path_when_overseas_would_fail(monkeypatch):
    overseas_calls = []

    def record_overseas(*args, **kwargs):
        overseas_calls.append(args)
        raise AssertionError("overseas adapter should not be called")

    for name in ("fetch_quote", "fetch_company_profile", "fetch_fundamentals", "fetch_earnings", "fetch_sec_fundamentals", "fetch_news"):
        monkeypatch.setattr(f"stock_daily_report.fisher.{name}", record_overseas)

    monkeypatch.setattr("stock_daily_report.fisher.fetch_cn_quote", lambda symbol, app: Quote(symbol, source="东方财富/腾讯财经公开行情"))
    monkeypatch.setattr("stock_daily_report.fisher.fetch_cn_company_profile", lambda symbol, app: CompanyProfile(symbol, "平安银行", source="东方财富 F10 公司资料"))
    monkeypatch.setattr("stock_daily_report.fisher.fetch_cn_fundamentals", lambda symbol, app: FundamentalSnapshot(symbol, source="东方财富 F10 主要指标"))
    monkeypatch.setattr("stock_daily_report.fisher.fetch_cn_news", lambda symbol, app, keywords, limit: [])
    monkeypatch.setattr("stock_daily_report.fisher.fetch_cn_filings", lambda symbol, app: SecFundamentalData(symbol, source="巨潮资讯 CNINFO/交易所公告索引"))

    analysis = build_fisher_analysis(Settings(), "000001")

    assert analysis.security.symbol == "000001"
    assert analysis.quote.source == "东方财富/腾讯财经公开行情"
    assert overseas_calls == []
