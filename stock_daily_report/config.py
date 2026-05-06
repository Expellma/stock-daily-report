"""Configuration loading for the daily stock report workflow."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class AppConfig:
    watchlist_path: Path = Path("config/watchlist.csv")
    sp500_path: Path = Path("config/sp500_symbols.csv")
    output_dir: Path = Path("outputs")
    max_watchlist_news: int = 4
    max_sp500_news: int = 12
    request_timeout_seconds: int = 10
    user_agent: str = "stock-daily-report/0.1"


@dataclass(frozen=True)
class ScheduleConfig:
    time: str = "08:00"
    timezone: str = "America/New_York"
    run_on_weekends: bool = False


@dataclass(frozen=True)
class PosterConfig:
    width: int = 1080
    height: int = 1440
    background: str = "#F7F8FA"
    primary: str = "#111827"
    muted: str = "#6B7280"
    accent: str = "#2563EB"
    positive: str = "#DC2626"
    negative: str = "#059669"


@dataclass(frozen=True)
class SignalConfig:
    major_keywords: list[str] = field(default_factory=lambda: ["earnings", "guidance", "merger"])


@dataclass(frozen=True)
class Settings:
    app: AppConfig = field(default_factory=AppConfig)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    poster: PosterConfig = field(default_factory=PosterConfig)
    signals: SignalConfig = field(default_factory=SignalConfig)


def _merge_dataclass(cls, values: dict) -> object:
    """Instantiate a config dataclass while ignoring unknown TOML keys."""

    allowed = cls.__dataclass_fields__.keys()  # type: ignore[attr-defined]
    return cls(**{key: value for key, value in values.items() if key in allowed})


def load_settings(path: str | Path = "config/settings.toml") -> Settings:
    """Load TOML settings and apply safe defaults for omitted sections."""

    config_path = Path(path)
    raw: dict = {}
    if config_path.exists():
        raw = _loads_toml(config_path.read_text(encoding="utf-8"))

    app = _merge_dataclass(AppConfig, raw.get("app", {}))
    app = AppConfig(
        watchlist_path=Path(app.watchlist_path),
        sp500_path=Path(app.sp500_path),
        output_dir=Path(app.output_dir),
        max_watchlist_news=app.max_watchlist_news,
        max_sp500_news=app.max_sp500_news,
        request_timeout_seconds=app.request_timeout_seconds,
        user_agent=app.user_agent,
    )
    return Settings(
        app=app,
        schedule=_merge_dataclass(ScheduleConfig, raw.get("schedule", {})),
        poster=_merge_dataclass(PosterConfig, raw.get("poster", {})),
        signals=_merge_dataclass(SignalConfig, raw.get("signals", {})),
    )


def _loads_toml(text: str) -> dict:
    """Parse the small settings TOML subset used by this local app."""

    data: dict[str, dict] = {}
    section: dict | None = None
    collecting_key: str | None = None
    collecting_values: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if collecting_key:
            if line == "]":
                section[collecting_key] = [_parse_scalar(item.rstrip(",").strip()) for item in collecting_values if item.rstrip(",").strip()]
                collecting_key = None
                collecting_values = []
            else:
                collecting_values.append(line)
            continue
        if line.startswith("[") and line.endswith("]"):
            section = data.setdefault(line[1:-1], {})
            continue
        if section is None or "=" not in line:
            continue
        key, value = [part.strip() for part in line.split("=", 1)]
        if value == "[":
            collecting_key = key
            collecting_values = []
        else:
            section[key] = _parse_scalar(value)
    return data


def _parse_scalar(value: str):
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    if value in {"true", "false"}:
        return value == "true"
    try:
        return int(value)
    except ValueError:
        return value
