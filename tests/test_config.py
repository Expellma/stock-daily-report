from pathlib import Path

from stock_daily_report.config import load_settings
from stock_daily_report.data_sources import score_news


def test_load_settings_defaults_for_missing_file(tmp_path: Path):
    settings = load_settings(tmp_path / "missing.toml")
    assert settings.schedule.time == "08:00"
    assert settings.app.output_dir == Path("outputs")


def test_news_scoring_prioritizes_earnings_and_guidance():
    plain = score_news("Stock market today: indexes drift", ["earnings", "guidance"])
    important = score_news("Company beats earnings and raises guidance", ["earnings", "guidance"])
    assert important > plain
