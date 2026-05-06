"""Minimal local scheduler for the daily report job."""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
import time
from zoneinfo import ZoneInfo

from .config import Settings
from .poster import render_poster
from .report import build_report, output_dir_for, write_report_json

LOGGER = logging.getLogger(__name__)


def run_once(settings: Settings) -> tuple[str, str]:
    """Generate one report JSON and poster, returning artifact paths."""

    report = build_report(settings)
    output_dir = output_dir_for(settings, report.generated_at)
    json_path = write_report_json(report, output_dir)
    poster_path = render_poster(report, settings.poster, output_dir)
    if report.errors:
        LOGGER.warning("Report generated with %s data-source warnings", len(report.errors))
    return str(json_path), str(poster_path)


def run_forever(settings: Settings) -> None:
    """Run the report job every configured morning in the local timezone."""

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    while True:
        next_run = _next_run_at(settings)
        sleep_seconds = max(1, int((next_run - datetime.now(next_run.tzinfo)).total_seconds()))
        LOGGER.info("Next stock report run scheduled for %s", next_run.isoformat())
        time.sleep(sleep_seconds)
        try:
            json_path, poster_path = run_once(settings)
            LOGGER.info("Generated report=%s poster=%s", json_path, poster_path)
        except Exception:  # noqa: BLE001 - keep long-running local scheduler alive after one bad run
            LOGGER.exception("Daily stock report job failed")


def _next_run_at(settings: Settings) -> datetime:
    hour, minute = [int(part) for part in settings.schedule.time.split(":", 1)]
    tz = ZoneInfo(settings.schedule.timezone)
    now = datetime.now(tz)
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    while not settings.schedule.run_on_weekends and candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate
