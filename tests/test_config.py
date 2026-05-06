from pathlib import Path

from stock_daily_report.config import AppConfig, load_settings
from stock_daily_report.data_sources import fetch_earnings, score_news


def test_load_settings_defaults_for_missing_file(tmp_path: Path):
    settings = load_settings(tmp_path / "missing.toml")
    assert settings.schedule.time == "08:00"
    assert settings.app.output_dir == Path("outputs")


def test_news_scoring_prioritizes_earnings_and_guidance():
    plain = score_news("Stock market today: indexes drift", ["earnings", "guidance"])
    important = score_news("Company beats earnings and raises guidance", ["earnings", "guidance"])
    assert important > plain


def test_fetch_earnings_handles_null_nasdaq_payload(monkeypatch):
    def fake_get_text(url: str, app_config: AppConfig) -> str:
        return "null"

    monkeypatch.setattr("stock_daily_report.data_sources._get_text", fake_get_text)

    event = fetch_earnings("AAPL", AppConfig())

    assert event.symbol == "AAPL"
    assert event.report_date is None
    assert event.error is None


def test_fetch_earnings_handles_null_nasdaq_data(monkeypatch):
    def fake_get_text(url: str, app_config: AppConfig) -> str:
        return '{"data": null}'

    monkeypatch.setattr("stock_daily_report.data_sources._get_text", fake_get_text)

    event = fetch_earnings("MSFT", AppConfig())

    assert event.symbol == "MSFT"
    assert event.report_date is None
    assert event.error is None
