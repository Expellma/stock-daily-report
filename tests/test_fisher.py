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
        profile=CompanyProfile(
            "NVDA",
            "NVIDIA",
            sector="Technology",
            industry="Semiconductors",
            summary="AI chips and platforms.",
        ),
        fundamentals=FundamentalSnapshot(
            "NVDA",
            metrics=[
                FundamentalMetric("收入增长", "20%", "Growth test", "positive"),
                FundamentalMetric("毛利率", "70%", "Margin test", "positive"),
            ],
        ),
        news=[
            NewsItem(
                "NVDA",
                "NVIDIA launches new AI platform",
                "https://example.com",
                published_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
            )
        ],
        earnings=EarningsEvent("NVDA", report_date="2026-05-20"),
        criteria=[
            FisherCriterion(
                1,
                "产品/服务是否拥有足够大的长期市场空间？",
                "question",
                "偏正面",
                ["evidence"],
                4,
            )
        ],
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
                    SecFactPoint(
                        "收入",
                        "Revenues",
                        "Q1",
                        2027,
                        "2026-04-26",
                        "2026-05-01",
                        "10-Q",
                        26000000000,
                        "USD",
                    ),
                    SecFactPoint(
                        "收入",
                        "Revenues",
                        "Q4",
                        2026,
                        "2026-01-25",
                        "2026-02-20",
                        "10-K",
                        22000000000,
                        "USD",
                    ),
                ],
                "稀释 EPS": [
                    SecFactPoint(
                        "稀释 EPS",
                        "EarningsPerShareDiluted",
                        "Q1",
                        2027,
                        "2026-04-26",
                        "2026-05-01",
                        "10-Q",
                        5.12,
                        "USD/shares",
                    )
                ],
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
    monkeypatch.setattr(
        "stock_daily_report.fisher.fetch_company_profile", fail_overseas
    )
    monkeypatch.setattr("stock_daily_report.fisher.fetch_fundamentals", fail_overseas)
    monkeypatch.setattr("stock_daily_report.fisher.fetch_earnings", fail_overseas)
    monkeypatch.setattr(
        "stock_daily_report.fisher.fetch_sec_fundamentals", fail_overseas
    )
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


def test_build_fisher_analysis_keeps_a_share_off_overseas_path_when_overseas_would_fail(
    monkeypatch,
):
    overseas_calls = []

    def record_overseas(*args, **kwargs):
        overseas_calls.append(args)
        raise AssertionError("overseas adapter should not be called")

    for name in (
        "fetch_quote",
        "fetch_company_profile",
        "fetch_fundamentals",
        "fetch_earnings",
        "fetch_sec_fundamentals",
        "fetch_news",
    ):
        monkeypatch.setattr(f"stock_daily_report.fisher.{name}", record_overseas)

    monkeypatch.setattr(
        "stock_daily_report.fisher.fetch_cn_quote",
        lambda symbol, app: Quote(symbol, source="东方财富/腾讯财经公开行情"),
    )
    monkeypatch.setattr(
        "stock_daily_report.fisher.fetch_cn_company_profile",
        lambda symbol, app: CompanyProfile(
            symbol, "平安银行", source="东方财富 F10 公司资料"
        ),
    )
    monkeypatch.setattr(
        "stock_daily_report.fisher.fetch_cn_fundamentals",
        lambda symbol, app: FundamentalSnapshot(symbol, source="东方财富 F10 主要指标"),
    )
    monkeypatch.setattr(
        "stock_daily_report.fisher.fetch_cn_news",
        lambda symbol, app, keywords, limit: [],
    )
    monkeypatch.setattr(
        "stock_daily_report.fisher.fetch_cn_filings",
        lambda symbol, app: SecFundamentalData(
            symbol, source="巨潮资讯 CNINFO/交易所公告索引"
        ),
    )

    analysis = build_fisher_analysis(Settings(), "000001")

    assert analysis.security.symbol == "000001"
    assert analysis.quote.source == "东方财富/腾讯财经公开行情"
    assert overseas_calls == []


def test_score_fisher_criteria_uses_chinese_annual_report_keywords():
    from stock_daily_report.fisher import _score_fisher_criteria

    criteria = _score_fisher_criteria(
        "2025年年度报告显示：研发费用同比增长，收到行政处罚，经营现金流为负。",
        [],
        [],
    )
    criteria_by_number = {criterion.number: criterion for criterion in criteria}

    innovation = criteria_by_number[2]
    assert innovation.score == 4
    assert any(
        "年报来源" in item and "研发费用" in item for item in innovation.evidence
    )

    cashflow_quality = criteria_by_number[13]
    assert cashflow_quality.score == 2
    assert any(
        "财务质量关键词（偏负面）" in item and "经营现金流" in item
        for item in cashflow_quality.evidence
    )

    governance_risk = criteria_by_number[15]
    assert governance_risk.score == 2
    assert any(
        "风险/治理关键词（偏负面）" in item and "行政处罚" in item
        for item in governance_risk.evidence
    )


def test_local_annual_report_evidence_is_loaded_and_rendered(tmp_path):
    from stock_daily_report.fisher import (
        load_local_annual_reports,
        _score_fisher_criteria,
    )

    report_dir = tmp_path / "贵州茅台"
    report_dir.mkdir()
    (report_dir / "2025.md").write_text(
        "年度报告：研发投入增加，毛利率提升，经营现金流改善。", encoding="utf-8"
    )
    (report_dir / "2025.pdf").write_bytes(b"%PDF-1.4")

    evidence = load_local_annual_reports(report_dir)
    criteria = _score_fisher_criteria("", [], [], annual_report_evidence=evidence)
    markdown = render_fisher_markdown(
        FisherAnalysis(
            generated_at=datetime(2026, 5, 6, tzinfo=timezone.utc),
            security=Security("600519.SH", "贵州茅台"),
            quote=Quote("600519.SH"),
            profile=CompanyProfile("600519.SH", "贵州茅台"),
            fundamentals=FundamentalSnapshot("600519.SH"),
            criteria=criteria,
            sec_data=SecFundamentalData(
                "600519.SH", source="巨潮资讯 CNINFO/交易所公告索引"
            ),
            annual_report_evidence=evidence,
        )
    )

    assert any(item.keyword == "研发投入" for item in evidence.items)
    assert any(item.status == "unsupported" for item in evidence.files)
    assert any(
        "年报来源" in item and "研发投入" in item for item in criteria[1].evidence
    )
    assert "## 📚 本地年报文件分析" in markdown
    assert "暂不支持直接解析 PDF" in markdown


def test_resolve_local_annual_report_dir_matches_symbol_case_insensitively(tmp_path):
    from stock_daily_report.fisher import resolve_local_annual_report_dir

    input_root = tmp_path / "input"
    report_dir = input_root / "nvda"
    report_dir.mkdir(parents=True)

    assert resolve_local_annual_report_dir("NVDA", input_root=input_root) == report_dir


def test_resolve_local_annual_report_dir_defaults_to_project_input(
    tmp_path, monkeypatch
):
    from stock_daily_report.fisher import resolve_local_annual_report_dir

    report_dir = tmp_path / "input" / "nvda"
    report_dir.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)

    assert resolve_local_annual_report_dir("NVDA") == report_dir


def test_build_fisher_analysis_defaults_to_project_input_symbol_dir_case_insensitively(
    tmp_path, monkeypatch
):
    input_dir = tmp_path / "input"
    report_dir = input_dir / "nvda"
    report_dir.mkdir(parents=True)
    (report_dir / "2025.md").write_text(
        "年度报告：research 投入增加，经营现金流改善。", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "stock_daily_report.fisher.fetch_quote",
        lambda symbol, app: Quote(symbol),
    )
    monkeypatch.setattr(
        "stock_daily_report.fisher.fetch_company_profile",
        lambda symbol, app: CompanyProfile(symbol, ""),
    )
    monkeypatch.setattr(
        "stock_daily_report.fisher.fetch_fundamentals",
        lambda symbol, app: FundamentalSnapshot(symbol),
    )
    monkeypatch.setattr(
        "stock_daily_report.fisher.fetch_earnings",
        lambda symbol, app: EarningsEvent(symbol),
    )
    monkeypatch.setattr(
        "stock_daily_report.fisher.fetch_sec_fundamentals",
        lambda symbol, app: SecFundamentalData(symbol),
    )
    monkeypatch.setattr(
        "stock_daily_report.fisher.fetch_news",
        lambda symbol, app, keywords, limit: [],
    )

    analysis = build_fisher_analysis(Settings(), "NVDA")

    assert analysis.annual_report_evidence.directory == str(report_dir)
    assert any(
        file.status == "loaded" for file in analysis.annual_report_evidence.files
    )


def test_ascii_keyword_matching_uses_word_boundaries():
    from stock_daily_report.fisher import _score_fisher_criteria

    criteria = _score_fisher_criteria(
        "Yahoo said management maintains security controls in the second quarter.",
        [],
        [],
    )
    criteria_by_number = {criterion.number: criterion for criterion in criteria}

    assert criteria_by_number[2].score == 3
    assert criteria_by_number[15].score == 3


def test_build_fisher_markdown_poster_from_local_chatgpt_md(tmp_path):
    from stock_daily_report.fisher import (
        build_fisher_analysis_from_markdown_reports,
        write_fisher_markdown_poster,
    )

    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    (report_dir / "chatgpt_analysis.md").write_text(
        """
# ChatGPT 财报分析

公司通过 AI 平台和新产品扩张市场份额，研发费用持续增加。
毛利率提升，经营现金流改善，但客户集中度和存货增加仍需关注。
品牌、供应链和规模优势构成护城河。
""".strip(),
        encoding="utf-8",
    )

    analysis = build_fisher_analysis_from_markdown_reports(
        report_dir,
        "NVDA",
        name="NVIDIA",
        thesis="AI 平台扩张",
    )
    poster_path = write_fisher_markdown_poster(analysis, tmp_path / "out")
    poster = poster_path.read_text(encoding="utf-8")

    assert poster_path.name == "nvda_fisher_poster.md"
    assert "NVIDIA（NVDA）费雪分析 Markdown 海报" in poster
    assert "本地 Markdown（不调用 GPT/API）" in poster
    assert "chatgpt_analysis.md" in poster
    assert "费雪 15 问评分矩阵" in poster
    assert any(
        item.keyword == "研发费用" for item in analysis.annual_report_evidence.items
    )
