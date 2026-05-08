from datetime import datetime, timezone
import json

from stock_daily_report.config import PosterConfig
from stock_daily_report.models import PdfReportAnalysis
from stock_daily_report.pdf_reports import (
    analyze_pdf_reports_with_chatgpt,
    discover_pdf_reports,
    write_pdf_report_analysis_json,
)
from stock_daily_report.poster import render_pdf_report_poster


def test_discover_pdf_reports_only_returns_pdfs(tmp_path):
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    (report_dir / "b.pdf").write_bytes(b"%PDF-1.4")
    (report_dir / "a.PDF").write_bytes(b"%PDF-1.4")
    (report_dir / "notes.md").write_text("ignore", encoding="utf-8")

    assert [path.name for path in discover_pdf_reports(report_dir)] == [
        "a.PDF",
        "b.pdf",
    ]


def test_analyze_pdf_reports_with_chatgpt_normalizes_payload(tmp_path, monkeypatch):
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    (report_dir / "annual.pdf").write_bytes(b"%PDF-1.4")

    def fake_call(pdf_files, *, symbol, name, thesis, model, api_key):
        assert [path.name for path in pdf_files] == ["annual.pdf"]
        assert symbol == "600519.SH"
        assert name == "贵州茅台"
        assert model == "test-model"
        return {
            "company_name": "贵州茅台",
            "period": "2025 年报",
            "title": "茅台年报速览",
            "subtitle": "现金流稳健",
            "verdict": "高端白酒需求仍需跟踪",
            "revenue": "营收同比增长",
            "profit": "归母净利增长",
            "cash_flow": "经营现金流改善",
            "margins": "毛利率保持高位",
            "growth_drivers": ["直销渠道", "品牌势能"],
            "risks": ["需求波动", "渠道库存"],
            "poster_bullets": ["现金流质量较好", "关注批价变化"],
            "sources": ["annual.pdf p12"],
        }

    monkeypatch.setattr(
        "stock_daily_report.pdf_reports._call_openai_responses", fake_call
    )

    analysis = analyze_pdf_reports_with_chatgpt(
        report_dir,
        "600519.SH",
        name="贵州茅台",
        model="test-model",
    )

    assert analysis.company_name == "贵州茅台"
    assert analysis.period == "2025 年报"
    assert analysis.files == ["annual.pdf"]
    assert analysis.poster_bullets == ["现金流质量较好", "关注批价变化"]


def test_pdf_report_analysis_json_and_poster_are_written(tmp_path):
    analysis = PdfReportAnalysis(
        generated_at=datetime(2026, 5, 8, tzinfo=timezone.utc),
        symbol="NVDA",
        company_name="NVIDIA",
        period="FY2026",
        title="NVIDIA 财报速览",
        subtitle="数据中心仍是主线",
        verdict="收入增长强劲，但需关注供给与客户集中度。",
        revenue="Revenue +50% YoY",
        profit="Net income +45% YoY",
        cash_flow="Operating cash flow positive",
        margins="Gross margin 70%",
        growth_drivers=["AI 加速卡需求", "软件生态扩张"],
        risks=["客户集中度", "供应链约束"],
        poster_bullets=["数据中心收入高增", "毛利率保持高位", "现金流支持投入"],
        sources=["annual.pdf p10"],
        report_dir="input/NVDA",
        files=["annual.pdf"],
        model="test-model",
    )

    json_path = write_pdf_report_analysis_json(analysis, tmp_path)
    poster_path = render_pdf_report_poster(analysis, PosterConfig(), tmp_path)

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    svg = poster_path.read_text(encoding="utf-8")

    assert payload["title"] == "NVIDIA 财报速览"
    assert "NVIDIA 财报速览" in svg
    assert "数据中心收入高增" in svg
    assert "annual.pdf p10" in svg
