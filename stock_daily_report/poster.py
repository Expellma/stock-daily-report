"""SVG poster rendering for social-media-friendly daily stock summaries."""

from __future__ import annotations

from html import escape
from pathlib import Path
import textwrap

from .config import PosterConfig
from .models import DailyReport, SecurityDigest


def render_poster(report: DailyReport, config: PosterConfig, output_dir: Path) -> Path:
    """Render a concise SVG poster from the generated report."""

    output_dir.mkdir(parents=True, exist_ok=True)
    margin = 64
    y = 70
    elements: list[str] = [
        _text(margin, y, "美股每日关注", 58, config.primary, weight=800),
        _text(margin, y + 64, report.generated_at.strftime("%Y-%m-%d %H:%M UTC"), 22, config.muted),
        f'<rect x="{margin}" y="{y + 105}" width="{config.width - margin * 2}" height="10" rx="5" fill="{config.accent}"/>',
    ]
    y += 160

    elements.append(_text(margin, y, "关注标的", 34, config.primary, weight=800))
    y += 44
    for digest in report.watchlist[:5]:
        card, y = _security_card(digest, y, margin, config)
        elements.extend(card)
        y += 18

    y += 8
    elements.append(_text(margin, y, "标普500重大新闻", 34, config.primary, weight=800))
    y += 46
    for item in report.sp500_news[:6]:
        headline = f"{item.symbol}｜{item.title}"
        for line in textwrap.wrap(headline, width=42)[:2]:
            elements.append(_text(margin, y, f"• {line}", 24, config.primary))
            y += 32
        y += 8
        if y > config.height - 120:
            break

    footer = "数据源：Yahoo Finance / Nasdaq；仅供研究参考，不构成投资建议"
    elements.append(_text(margin, config.height - 72, footer, 21, config.muted))
    svg = "\n".join(
        [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{config.width}" height="{config.height}" viewBox="0 0 {config.width} {config.height}">',
            f'<rect width="100%" height="100%" fill="{config.background}"/>',
            *elements,
            "</svg>",
        ]
    )
    path = output_dir / "daily_poster.svg"
    path.write_text(svg, encoding="utf-8")
    return path


def _security_card(digest: SecurityDigest, y: int, margin: int, config: PosterConfig) -> tuple[list[str], int]:
    card_right = config.width - margin
    card_bottom = y + 130
    quote = digest.quote
    change = quote.change_percent
    change_text = "N/A" if change is None else f"{change:+.2f}%"
    price_text = "--" if quote.price is None else f"${quote.price:,.2f}"
    change_color = config.muted if change is None else (config.positive if change >= 0 else config.negative)
    catalyst = _first_catalyst(digest)

    elements = [
        f'<rect x="{margin}" y="{y}" width="{card_right - margin}" height="130" rx="24" fill="#FFFFFF"/>',
        _text(margin + 24, y + 38, f"{digest.security.symbol}  {digest.security.name}", 28, config.primary, weight=800),
        _text(card_right - 260, y + 38, price_text, 28, config.primary, weight=800),
        _text(card_right - 130, y + 84, change_text, 26, change_color, weight=800),
    ]
    for idx, line in enumerate(textwrap.wrap(catalyst, width=52)[:2]):
        elements.append(_text(margin + 24, y + 88 + idx * 26, line, 21, config.muted))
    return elements, card_bottom


def _first_catalyst(digest: SecurityDigest) -> str:
    if digest.earnings and digest.earnings.report_date:
        return f"财报关注：{digest.earnings.report_date}，EPS预期 {digest.earnings.estimate or 'N/A'}"
    if digest.news:
        return f"催化：{digest.news[0].title}"
    return f"投资主线：{digest.security.thesis or '等待高质量催化'}"


def _text(x: int, y: int, value: str, size: int, color: str, weight: int = 400) -> str:
    return f'<text x="{x}" y="{y}" fill="{color}" font-family="Arial, Helvetica, sans-serif" font-size="{size}" font-weight="{weight}">{escape(value)}</text>'
