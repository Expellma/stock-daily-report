"""SVG poster rendering for social-media-friendly daily stock summaries."""

from __future__ import annotations

from html import escape
from pathlib import Path
import textwrap

from .config import PosterConfig
from .models import DailyReport, PdfReportAnalysis, SecurityDigest


def render_poster(report: DailyReport, config: PosterConfig, output_dir: Path) -> Path:
    """Render a concise SVG poster from the generated report."""

    output_dir.mkdir(parents=True, exist_ok=True)
    margin = 64
    y = 70
    elements: list[str] = [
        _text(margin, y, "美股每日关注", 58, config.primary, weight=800),
        _text(
            margin,
            y + 64,
            report.generated_at.strftime("%Y-%m-%d %H:%M UTC"),
            22,
            config.muted,
        ),
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


def _security_card(
    digest: SecurityDigest, y: int, margin: int, config: PosterConfig
) -> tuple[list[str], int]:
    card_right = config.width - margin
    card_bottom = y + 130
    quote = digest.quote
    change = quote.change_percent
    change_text = "N/A" if change is None else f"{change:+.2f}%"
    price_text = "--" if quote.price is None else f"${quote.price:,.2f}"
    change_color = (
        config.muted
        if change is None
        else (config.positive if change >= 0 else config.negative)
    )
    catalyst = _first_catalyst(digest)

    elements = [
        f'<rect x="{margin}" y="{y}" width="{card_right - margin}" height="130" rx="24" fill="#FFFFFF"/>',
        _text(
            margin + 24,
            y + 38,
            f"{digest.security.symbol}  {digest.security.name}",
            28,
            config.primary,
            weight=800,
        ),
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


def render_pdf_report_poster(
    analysis: PdfReportAnalysis, config: PosterConfig, output_dir: Path
) -> Path:
    """Render an SVG poster from ChatGPT analysis of local PDF financial reports."""

    output_dir.mkdir(parents=True, exist_ok=True)
    margin = 64
    card_width = config.width - margin * 2
    y = 72
    elements: list[str] = [
        _text(margin, y, analysis.title, 52, config.primary, weight=800),
        f'<rect x="{margin}" y="{y + 102}" width="{card_width}" height="10" rx="5" fill="{config.accent}"/>',
    ]
    elements.extend(
        _wrapped_text(margin, y + 54, analysis.subtitle, 25, config.muted, 38)
    )
    y += 154
    elements.extend(
        _info_card(
            margin,
            y,
            card_width,
            "核心结论",
            analysis.verdict,
            config,
            height=148,
            accent=config.accent,
        )
    )
    y += 172

    metric_rows = [
        ("营收", analysis.revenue),
        ("利润", analysis.profit),
        ("现金流", analysis.cash_flow),
        ("利润率", analysis.margins),
    ]
    elements.append(_text(margin, y, "关键财务信号", 34, config.primary, weight=800))
    y += 28
    for idx, (label, value) in enumerate(metric_rows):
        x = margin + (idx % 2) * (card_width // 2 + 12)
        row_y = y + (idx // 2) * 142
        elements.extend(
            _mini_metric_card(x, row_y, card_width // 2 - 12, label, value, config)
        )
    y += 300

    elements.append(_text(margin, y, "增长动因", 32, config.primary, weight=800))
    elements.append(
        _text(
            margin + card_width // 2 + 18, y, "主要风险", 32, config.primary, weight=800
        )
    )
    y += 44
    elements.extend(
        _bullet_column(
            margin,
            y,
            card_width // 2 - 18,
            analysis.growth_drivers,
            config.positive,
            config,
        )
    )
    elements.extend(
        _bullet_column(
            margin + card_width // 2 + 18,
            y,
            card_width // 2 - 18,
            analysis.risks,
            config.negative,
            config,
        )
    )
    y += 260

    elements.append(_text(margin, y, "海报要点", 32, config.primary, weight=800))
    y += 42
    for bullet in analysis.poster_bullets[:5]:
        for line in textwrap.wrap(bullet, width=36)[:2]:
            elements.append(_text(margin, y, f"• {line}", 25, config.primary))
            y += 34
        y += 6
        if y > config.height - 170:
            break

    source = "；".join(analysis.sources[:2]) or "本地 PDF 财报"
    footer_lines = [
        f"来源：{source}",
        f"模型：{analysis.model or 'ChatGPT'} · 生成：{analysis.generated_at.strftime('%Y-%m-%d %H:%M UTC')}",
        analysis.disclaimer,
    ]
    footer_y = config.height - 112
    for line in footer_lines:
        elements.append(
            _text(
                margin,
                footer_y,
                textwrap.shorten(line, width=58, placeholder="..."),
                20,
                config.muted,
            )
        )
        footer_y += 28

    svg = "\n".join(
        [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{config.width}" height="{config.height}" viewBox="0 0 {config.width} {config.height}">',
            f'<rect width="100%" height="100%" fill="{config.background}"/>',
            *elements,
            "</svg>",
        ]
    )
    path = output_dir / f"{analysis.symbol.lower()}_pdf_report_poster.svg"
    path.write_text(svg, encoding="utf-8")
    return path


def _info_card(
    x: int,
    y: int,
    width: int,
    title: str,
    body: str,
    config: PosterConfig,
    *,
    height: int,
    accent: str,
) -> list[str]:
    elements = [
        f'<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="26" fill="#FFFFFF"/>',
        f'<rect x="{x}" y="{y}" width="10" height="{height}" rx="5" fill="{accent}"/>',
        _text(x + 28, y + 40, title, 25, config.muted, weight=700),
    ]
    elements.extend(
        _wrapped_text(x + 28, y + 86, body, 31, config.primary, 31, weight=800)
    )
    return elements


def _mini_metric_card(
    x: int, y: int, width: int, label: str, value: str, config: PosterConfig
) -> list[str]:
    elements = [
        f'<rect x="{x}" y="{y}" width="{width}" height="118" rx="22" fill="#FFFFFF"/>',
        _text(x + 22, y + 36, label, 23, config.muted, weight=700),
    ]
    elements.extend(
        _wrapped_text(x + 22, y + 72, value, 24, config.primary, 22, weight=800)
    )
    return elements


def _bullet_column(
    x: int,
    y: int,
    width: int,
    bullets: list[str],
    color: str,
    config: PosterConfig,
) -> list[str]:
    elements = [
        f'<rect x="{x}" y="{y - 30}" width="{width}" height="240" rx="24" fill="#FFFFFF"/>'
    ]
    cursor = y + 10
    for bullet in bullets[:4]:
        elements.append(
            f'<circle cx="{x + 22}" cy="{cursor - 7}" r="7" fill="{color}"/>'
        )
        for line in textwrap.wrap(bullet, width=20)[:2]:
            elements.append(_text(x + 42, cursor, line, 22, config.primary))
            cursor += 29
        cursor += 8
    return elements


def _wrapped_text(
    x: int,
    y: int,
    value: str,
    size: int,
    color: str,
    width: int,
    weight: int = 400,
) -> list[str]:
    return [
        _text(x, y + idx * int(size * 1.25), line, size, color, weight=weight)
        for idx, line in enumerate(textwrap.wrap(value, width=width) or [""])
    ]
