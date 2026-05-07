"""Fisher growth-investing fundamental analysis and Markdown rendering."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
import textwrap

from .config import Settings
from .data_sources import fetch_company_profile, fetch_earnings, fetch_fundamentals, fetch_news, fetch_quote, fetch_sec_fundamentals
from .models import AnnualReportEvidence, AnnualReportEvidenceItem, AnnualReportFile, FisherAnalysis, FisherCriterion, FundamentalMetric, NewsItem, SecFactPoint, SecFundamentalData, Security
from .report import output_dir_for

FISHER_FRAMEWORK: tuple[tuple[str, str], ...] = (
    ("产品/服务是否拥有足够大的长期市场空间？", "看公司主营业务、行业位置与增长新闻，判断 TAM 是否仍可扩张。"),
    ("管理层是否有持续开发新产品和新流程的决心？", "关注新品、研发、平台扩展、并购整合和长期战略投入。"),
    ("研发投入相对规模是否有效？", "公开数据有限时，以创新新闻、产品迭代和技术壁垒作为替代线索。"),
    ("销售组织是否足够强？", "用收入增长、客户/渠道新闻和订单动能评估商业化能力。"),
    ("利润率是否具有吸引力？", "用毛利率、营业利润率和净利率观察产品差异化与经营杠杆。"),
    ("公司是否在主动维护或提升利润率？", "关注成本控制、定价权、规模效应和费用纪律。"),
    ("劳资/人才关系是否健康？", "公开数据有限时，记录为待调研，并提示通过员工口碑、流失率验证。"),
    ("高管关系和治理氛围是否健康？", "观察管理层稳定性、战略一致性和重大争议。"),
    ("管理层深度是否足够？", "判断公司是否过度依赖单一人物，以及关键业务是否有梯队。"),
    ("成本分析和会计控制是否可靠？", "用现金流、利润率和财务杠杆交叉验证会计质量。"),
    ("是否拥有同行难以复制的业务特征？", "寻找网络效应、规模优势、品牌、专利、生态或供应链壁垒。"),
    ("公司是否以长期利润为导向？", "结合投资主线、资本开支、回购/分红和研发投入线索。"),
    ("未来融资是否会显著稀释股东？", "以自由现金流、负债/权益和市值融资能力判断。"),
    ("管理层在顺境和逆境中是否坦诚？", "需要会议纪要/股东信验证；报告中列出需要进一步核验的问题。"),
    ("管理层诚信是否经得起检验？", "关注监管、诉讼、会计重述和重大争议新闻。"),
)


def build_fisher_analysis(settings: Settings, symbol: str, name: str | None = None, thesis: str = "", annual_report_dir: Path | None = None) -> FisherAnalysis:
    """Build a Fisher-style growth fundamental analysis for one symbol."""

    normalized_symbol = symbol.strip().upper()
    errors: list[str] = []
    quote = fetch_quote(normalized_symbol, settings.app)
    profile = fetch_company_profile(normalized_symbol, settings.app)
    fundamentals = fetch_fundamentals(normalized_symbol, settings.app)
    earnings = fetch_earnings(normalized_symbol, settings.app)
    sec_data = fetch_sec_fundamentals(normalized_symbol, settings.app)
    news = fetch_news(normalized_symbol, settings.app, settings.signals.major_keywords, settings.app.max_watchlist_news)

    if quote.error:
        errors.append(f"{normalized_symbol} quote: {quote.error}")
    if profile.error:
        errors.append(f"{normalized_symbol} profile: {profile.error}")
    if fundamentals.error:
        errors.append(f"{normalized_symbol} fundamentals: {fundamentals.error}")
    if earnings.error:
        errors.append(f"{normalized_symbol} earnings: {earnings.error}")
    if sec_data.error:
        errors.append(f"{normalized_symbol} SEC EDGAR: {sec_data.error}")
    errors.extend(f"{item.symbol} news: {item.title}" for item in news if item.score < 0)

    security = Security(symbol=normalized_symbol, name=name or profile.name or normalized_symbol, thesis=thesis)
    resolved_report_dir = annual_report_dir or Path("/input") / (name or normalized_symbol)
    annual_report_evidence = load_local_annual_reports(resolved_report_dir)
    clean_news = [item for item in news if item.score >= 0]
    return FisherAnalysis(
        generated_at=datetime.now(timezone.utc),
        security=security,
        quote=quote,
        profile=profile,
        fundamentals=fundamentals,
        news=clean_news,
        earnings=earnings,
        criteria=_score_fisher_criteria(profile.summary, fundamentals.metrics, clean_news, sec_data, annual_report_evidence),
        errors=errors,
        sec_data=sec_data,
        annual_report_evidence=annual_report_evidence,
    )


def write_fisher_markdown(analysis: FisherAnalysis, output_dir: Path) -> Path:
    """Persist a browser-friendly Markdown report for the Fisher analysis."""

    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{analysis.security.symbol.lower()}_fisher_analysis.md"
    path.write_text(render_fisher_markdown(analysis), encoding="utf-8")
    return path


def output_fisher_dir_for(settings: Settings, generated_at: datetime) -> Path:
    return output_dir_for(settings, generated_at) / "fisher"



ANNUAL_REPORT_KEYWORDS: tuple[str, ...] = (
    "研发投入",
    "研发费用",
    "毛利率",
    "经营现金流",
    "客户集中度",
    "存货",
    "应收账款",
    "诉讼",
    "监管处罚",
    "关联交易",
)
SUPPORTED_ANNUAL_REPORT_SUFFIXES = {".txt", ".md", ".pdf"}


def load_local_annual_reports(report_dir: Path) -> AnnualReportEvidence:
    """Load local annual-report snippets from a directory without blocking report generation."""

    report_dir = report_dir.expanduser()
    warnings: list[str] = []
    files: list[AnnualReportFile] = []
    evidence: list[AnnualReportEvidenceItem] = []
    if not report_dir.exists():
        warnings.append(f"本地年报目录不存在：{report_dir}")
        return AnnualReportEvidence(report_dir=str(report_dir), files=files, evidence=evidence, warnings=warnings)
    if not report_dir.is_dir():
        warnings.append(f"本地年报路径不是目录：{report_dir}")
        return AnnualReportEvidence(report_dir=str(report_dir), files=files, evidence=evidence, warnings=warnings)

    candidates = sorted(path for path in report_dir.iterdir() if path.is_file() and path.suffix.lower() in SUPPORTED_ANNUAL_REPORT_SUFFIXES)
    if not candidates:
        warnings.append(f"本地年报目录为空，或未找到 .txt/.md/.pdf 文件：{report_dir}")
        return AnnualReportEvidence(report_dir=str(report_dir), files=files, evidence=evidence, warnings=warnings)

    for path in candidates:
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            reason = "暂不解析 PDF；请先转换为 .txt 或 .md，PDF 解析可作为后续增强。"
            files.append(AnnualReportFile(path.name, str(path), suffix.lstrip("."), "unsupported", error=reason))
            warnings.append(f"{path.name}: {reason}")
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                content = path.read_text(encoding="utf-8-sig")
            except UnicodeDecodeError as exc:
                reason = f"文本编码无法解析：{exc}"
                files.append(AnnualReportFile(path.name, str(path), suffix.lstrip("."), "error", error=reason))
                warnings.append(f"{path.name}: {reason}")
                continue
        except OSError as exc:
            reason = f"读取失败：{exc}"
            files.append(AnnualReportFile(path.name, str(path), suffix.lstrip("."), "error", error=reason))
            warnings.append(f"{path.name}: {reason}")
            continue

        stripped = content.strip()
        if not stripped:
            reason = "文件内容为空。"
            files.append(AnnualReportFile(path.name, str(path), suffix.lstrip("."), "empty", error=reason))
            warnings.append(f"{path.name}: {reason}")
            continue
        files.append(AnnualReportFile(path.name, str(path), suffix.lstrip("."), "loaded", content=stripped))
        evidence.extend(_extract_annual_report_evidence(path.name, stripped))

    return AnnualReportEvidence(report_dir=str(report_dir), files=files, evidence=evidence, warnings=warnings)


def _extract_annual_report_evidence(filename: str, content: str) -> list[AnnualReportEvidenceItem]:
    evidence: list[AnnualReportEvidenceItem] = []
    seen: set[tuple[str, str]] = set()
    for keyword in ANNUAL_REPORT_KEYWORDS:
        for match in re.finditer(re.escape(keyword), content):
            start = max(0, match.start() - 45)
            end = min(len(content), match.end() + 75)
            excerpt = re.sub(r"\s+", " ", content[start:end]).strip()
            key = (keyword, excerpt)
            if key in seen:
                continue
            seen.add(key)
            evidence.append(AnnualReportEvidenceItem(keyword=keyword, filename=filename, excerpt=excerpt))
            break
    return evidence


def render_fisher_markdown(analysis: FisherAnalysis) -> str:
    """Render an elegant Markdown report that is easy to browse in GitHub or Notion."""

    score = _overall_score(analysis.criteria)
    thesis = analysis.security.thesis or "未提供自定义投资主线；以下结论仅基于公开行情、基本面与新闻线索。"
    sections = [
        f"# {analysis.security.name}（{analysis.security.symbol}）费雪成长投资基本面分析",
        "",
        f"> 生成时间：{analysis.generated_at.isoformat()} · 框架：Philip Fisher 15 个成长股问题 · SEC 近一年 10-K/10-Q 财报",
        "",
        "## 🧭 一页结论",
        "",
        f"**综合评分：{score}/5**  ",
        f"**投资主线：** {thesis}",
        "",
        _summary_callout(score),
        "",
        "## 🧾 公司画像",
        "",
        _company_table(analysis),
        "",
        "## 📊 关键基本面仪表盘",
        "",
        _metrics_table(analysis.fundamentals.metrics),
        "",
        "## 🏛️ SEC EDGAR 近一年财报数据",
        "",
        _sec_filings_table(analysis.sec_data),
        "",
        "### 📈 关键数据图标",
        "",
        _sec_key_data_panel(analysis.sec_data),
        "",
        "## 📁 本地年报文件分析",
        "",
        _annual_report_section(analysis.annual_report_evidence),
        "",
        "## 🐟 费雪 15 问逐项检查",
        "",
        _criteria_table(analysis.criteria),
        "",
        "## 📰 近期高信号新闻",
        "",
        _news_list(analysis.news),
        "",
        "## 🔍 下一步尽调清单",
        "",
        _due_diligence_list(analysis),
        "",
        "## ⚠️ 数据限制与风险提示",
        "",
        _risk_notes(analysis),
        "",
    ]
    return "\n".join(sections)


def _score_fisher_criteria(summary: str, metrics: list[FundamentalMetric], news: list[NewsItem], sec_data: SecFundamentalData | None = None, annual_report_evidence: AnnualReportEvidence | None = None) -> list[FisherCriterion]:
    metric_status = {metric.label: metric.status for metric in metrics}
    annual_evidence_text = " ".join(item.excerpt for item in (annual_report_evidence.evidence if annual_report_evidence else []))
    evidence_text = " ".join([summary, annual_evidence_text, *[item.title for item in news]]).lower()
    sec_change = _sec_change_map(sec_data)
    criteria: list[FisherCriterion] = []
    for index, (title, question) in enumerate(FISHER_FRAMEWORK, start=1):
        score = 3
        evidence: list[str] = []
        if index in {1, 4}:
            score += _status_points(metric_status.get("收入增长"))
            score += _change_points(sec_change.get("收入"))
            evidence.append(_metric_sentence(metrics, "收入增长"))
            evidence.append(_sec_change_sentence(sec_data, "收入"))
        if index in {5, 6, 10}:
            for label in ("毛利率", "营业利润率", "净利率"):
                score += _status_points(metric_status.get(label))
                evidence.append(_metric_sentence(metrics, label))
            for sec_label in ("毛利润", "营业利润", "净利润"):
                score += _change_points(sec_change.get(sec_label))
                evidence.append(_sec_change_sentence(sec_data, sec_label))
        if index == 13:
            score += _status_points(metric_status.get("自由现金流")) + _status_points(metric_status.get("负债/权益"))
            score += _change_points(sec_change.get("经营现金流"))
            evidence.extend([_metric_sentence(metrics, "自由现金流"), _metric_sentence(metrics, "负债/权益"), _sec_change_sentence(sec_data, "经营现金流")])
        if index in {2, 3, 11, 12} and re.search(r"\b(ai|cloud|chip|platform|patent|launch|partnership|r&d|research)\b|研发投入|研发费用|专利|新品|平台|合作", evidence_text):
            score += 1
            evidence.append("公开信息或本地年报包含创新、研发投入、平台扩展或合作相关线索。")
        if index in {5, 6, 10} and re.search(r"毛利率|经营现金流|存货|应收账款", evidence_text):
            evidence.append("本地年报出现毛利率、经营现金流、存货或应收账款等经营质量关键词。")
        if index in {4, 8, 14, 15} and re.search(r"客户集中度|诉讼|监管处罚|关联交易|\b(sec|lawsuit|probe|fraud|restatement|investigation)\b", evidence_text):
            score -= 1
            evidence.append("公开信息或本地年报出现客户集中度、诉讼、监管处罚、关联交易或诚信相关关键词，需优先核验。")
        if not evidence:
            evidence.append("公开量化数据不足，建议通过年报、电话会和专家访谈补足。")
        score = min(5, max(1, score))
        criteria.append(FisherCriterion(index, title, question, _assessment_text(score), evidence, score))
    return criteria


def _sec_change_map(sec_data: SecFundamentalData | None) -> dict[str, float | None]:
    if not sec_data:
        return {}
    changes: dict[str, float | None] = {}
    for label, points in sec_data.facts.items():
        latest = points[0] if points else None
        previous = points[1] if len(points) > 1 else None
        if latest:
            changes[label] = _fact_change(latest, previous)
    return changes


def _sec_change_sentence(sec_data: SecFundamentalData | None, label: str) -> str:
    points = sec_data.facts.get(label, []) if sec_data else []
    if not points:
        return f"SEC 近一年 {label} XBRL 字段暂无可用数据。"
    latest = points[0]
    previous = points[1] if len(points) > 1 else None
    change = _fact_change(latest, previous)
    if change is None:
        return f"SEC 最新 {label}为 {_format_fact_value(latest)}（报告期末 {latest.end_date or 'N/A'}）。"
    return f"SEC 最新 {label}为 {_format_fact_value(latest)}，较上一可比披露 {_format_change(change)}。"


def _change_points(change: float | None) -> int:
    if change is None:
        return 0
    if change >= 0.05:
        return 1
    if change <= -0.05:
        return -1
    return 0


def _company_table(analysis: FisherAnalysis) -> str:
    q = analysis.quote
    rows = [
        ("行业", analysis.profile.sector or "N/A"),
        ("细分赛道", analysis.profile.industry or "N/A"),
        ("官网", analysis.profile.website or "N/A"),
        ("最新价格", _format_price(q.price)),
        ("日涨跌幅", _format_percent(q.change_percent)),
        ("下次财报", analysis.earnings.report_date if analysis.earnings and analysis.earnings.report_date else "N/A"),
    ]
    if analysis.profile.summary:
        rows.append(("业务摘要", textwrap.shorten(analysis.profile.summary, width=220, placeholder="...")))
    return _markdown_table(["项目", "内容"], rows)


def _metrics_table(metrics: list[FundamentalMetric]) -> str:
    if not metrics:
        return "> 暂无可用基本面指标。"
    rows = [(metric.label, metric.value, _status_badge(metric.status), metric.interpretation) for metric in metrics]
    return _markdown_table(["指标", "数值", "状态", "解读"], rows)



def _sec_filings_table(sec_data: SecFundamentalData) -> str:
    if sec_data.error and not sec_data.filings:
        return f"> SEC EDGAR 数据获取失败：{_escape_md(sec_data.error)}"
    if not sec_data.filings:
        return "> 近一年未在 SEC EDGAR submissions 中找到 10-K/10-Q 财报。"
    rows = []
    for filing in sec_data.filings:
        link = f"[打开 SEC 文件]({filing.url})" if filing.url else "N/A"
        rows.append((filing.form, filing.filing_date or "N/A", filing.report_date or "N/A", filing.description or filing.primary_document, link))
    return _markdown_table(["表格", "提交日", "报告期", "说明", "来源"], rows)


def _sec_key_data_panel(sec_data: SecFundamentalData) -> str:
    if not sec_data.facts:
        return "> 暂无可图表化的 SEC XBRL companyfacts 数据。"
    preferred = ["收入", "毛利润", "营业利润", "净利润", "稀释 EPS", "经营现金流", "资本开支", "研发费用", "总资产", "总负债", "股东权益"]
    rows = []
    for label in preferred:
        points = sec_data.facts.get(label, [])
        if not points:
            continue
        latest = points[0]
        previous = points[1] if len(points) > 1 else None
        change = _fact_change(latest, previous)
        rows.append((
            _metric_icon(label, change),
            label,
            _format_fact_value(latest),
            latest.end_date or latest.filed_date or "N/A",
            _format_change(change),
            _sparkline(points),
        ))
    return _markdown_table(["图标", "关键数据", "最新值", "报告期末", "环比/同比线索", "近一年趋势"], rows)


def _fact_change(latest: SecFactPoint, previous: SecFactPoint | None) -> float | None:
    if previous is None or previous.value == 0:
        return None
    return (latest.value - previous.value) / abs(previous.value)


def _metric_icon(label: str, change: float | None) -> str:
    base = {
        "收入": "💰",
        "毛利润": "🏷️",
        "营业利润": "🏭",
        "净利润": "📌",
        "稀释 EPS": "🧮",
        "经营现金流": "💵",
        "资本开支": "🏗️",
        "研发费用": "🔬",
        "总资产": "🏦",
        "总负债": "🧱",
        "股东权益": "🧾",
    }.get(label, "📊")
    if change is None:
        return base
    if change > 0.03:
        return f"{base} ↗️"
    if change < -0.03:
        return f"{base} ↘️"
    return f"{base} ➡️"


def _format_fact_value(point: SecFactPoint) -> str:
    if point.unit == "USD/shares":
        return f"${point.value:,.2f}/share"
    abs_value = abs(point.value)
    if abs_value >= 1_000_000_000:
        return f"${point.value / 1_000_000_000:,.2f}B"
    if abs_value >= 1_000_000:
        return f"${point.value / 1_000_000:,.2f}M"
    return f"${point.value:,.0f}"


def _format_change(change: float | None) -> str:
    if change is None:
        return "N/A"
    marker = "🟢" if change > 0 else "🔴" if change < 0 else "⚪"
    return f"{marker} {change:+.1%}"


def _sparkline(points: list[SecFactPoint]) -> str:
    ordered = list(reversed(points[:6]))
    values = [point.value for point in ordered]
    if len(values) == 1:
        return "▁"
    low = min(values)
    high = max(values)
    blocks = "▁▂▃▄▅▆▇█"
    if high == low:
        return "".join("▄" for _ in values)
    return "".join(blocks[round((value - low) / (high - low) * (len(blocks) - 1))] for value in values)


def _annual_report_section(annual_report_evidence: AnnualReportEvidence) -> str:
    if not annual_report_evidence.report_dir:
        return "> 未配置本地年报目录；可使用 --annual-report-dir 指定 .txt/.md 年报片段目录。"
    lines = [f"**本地来源目录：** `{annual_report_evidence.report_dir}`", ""]
    if annual_report_evidence.files:
        rows = []
        for file in annual_report_evidence.files:
            status = {
                "loaded": "✅ 已读取",
                "unsupported": "⚠️ 暂不支持",
                "empty": "⚠️ 空文件",
                "error": "⚠️ 读取失败",
            }.get(file.status, file.status)
            rows.append((file.filename, file.file_type, status, file.error or "已纳入本地年报证据池"))
        lines.append(_markdown_table(["文件名", "类型", "状态", "说明"], rows))
    else:
        lines.append("> 未读取到可用的本地年报文件。")
    if annual_report_evidence.evidence:
        lines.extend(["", "**提取到的关键证据：**"])
        for item in annual_report_evidence.evidence[:20]:
            lines.append(f"- `{_escape_md(item.filename)}` · **{_escape_md(item.keyword)}**：{_escape_md(item.excerpt)}")
    else:
        lines.extend(["", "> 暂未从本地年报提取到预设关键词证据。"])
    if annual_report_evidence.warnings:
        lines.extend(["", "**无法解析或需要注意的文件/目录：**"])
        lines.extend(f"- {_escape_md(warning)}" for warning in annual_report_evidence.warnings)
    return "\n".join(lines)


def _criteria_table(criteria: list[FisherCriterion]) -> str:
    rows = []
    for item in criteria:
        rows.append((f"{item.number}. {item.title}", f"{item.score}/5" if item.score else "N/A", item.assessment, "<br>".join(item.evidence)))
    return _markdown_table(["费雪问题", "评分", "判断", "证据/待验证点"], rows)


def _news_list(news: list[NewsItem]) -> str:
    if not news:
        return "> 暂无高信号新闻；建议补充公司公告、10-K/10-Q、电话会纪要。"
    lines = []
    for item in news:
        date = item.published_at.date().isoformat() if item.published_at else "日期未知"
        title = _escape_md(item.title)
        link = item.link or "#"
        lines.append(f"- **{date}** [{title}]({link})")
    return "\n".join(lines)


def _due_diligence_list(analysis: FisherAnalysis) -> str:
    return "\n".join(
        [
            "- 阅读最近 2 次 10-K/10-Q，拆分收入增长的价格、销量、产品和地区贡献。",
            "- 对照电话会纪要核验管理层是否持续解释长期投入、利润率改善路径和风险。",
            "- 访谈客户、供应商或渠道伙伴，验证产品差异化与销售组织质量。",
            "- 跟踪竞争对手毛利率、研发强度与新品节奏，确认护城河是否扩大。",
            f"- 围绕“{analysis.security.thesis or analysis.security.name}”建立 3-5 个可证伪的季度观察指标。",
        ]
    )


def _risk_notes(analysis: FisherAnalysis) -> str:
    notes = ["- 本报告为自动化初筛，不构成投资建议；评分用于组织尽调优先级，不应单独作为买卖依据。"]
    if analysis.errors:
        notes.append("- 部分数据源返回异常：" + "; ".join(_escape_md(error) for error in analysis.errors))
    notes.append("- SEC 数据来自 EDGAR submissions/companyfacts（与 SEC EDGAR Search 同源）；XBRL 标签可能因公司披露口径不同而缺失或不可比。")
    notes.append("- Yahoo/Nasdaq 公共接口字段可能滞后或缺失；关键结论需用公司公告与 SEC 文件复核。")
    return "\n".join(notes)


def _summary_callout(score: int) -> str:
    if score >= 4:
        return "> ✅ **初筛结论：** 基本面质量与成长线索较强，可进入深度尽调与估值情景分析。"
    if score >= 3:
        return "> 🟡 **初筛结论：** 存在可研究的成长线索，但仍需补充管理层、竞争格局和估值验证。"
    return "> 🔴 **初筛结论：** 当前公开数据未形成足够强的费雪式成长证据，建议先列入观察。"


def _overall_score(criteria: list[FisherCriterion]) -> int:
    scores = [item.score for item in criteria if item.score is not None]
    return round(sum(scores) / len(scores)) if scores else 0


def _metric_sentence(metrics: list[FundamentalMetric], label: str) -> str:
    for metric in metrics:
        if metric.label == label:
            return f"{label}为 {metric.value}（{_status_badge(metric.status)}）。"
    return f"{label}暂无公开字段，需补充核验。"


def _status_points(status: str | None) -> int:
    return {"positive": 1, "negative": -1}.get(status or "neutral", 0)


def _assessment_text(score: int) -> str:
    if score >= 5:
        return "强正面"
    if score == 4:
        return "偏正面"
    if score == 3:
        return "中性/待验证"
    if score == 2:
        return "偏负面"
    return "明显不足"


def _status_badge(status: str) -> str:
    return {"positive": "🟢 正面", "negative": "🔴 风险", "neutral": "⚪ 中性"}.get(status, "⚪ 中性")


def _format_price(value: float | None) -> str:
    return "N/A" if value is None else f"${value:,.2f}"


def _format_percent(value: float | None) -> str:
    return "N/A" if value is None else f"{value:+.2f}%"


def _markdown_table(headers: list[str], rows: list[tuple[object, ...]]) -> str:
    header = "| " + " | ".join(headers) + " |"
    separator = "| " + " | ".join("---" for _ in headers) + " |"
    body = ["| " + " | ".join(_escape_md(str(cell)) for cell in row) + " |" for row in rows]
    return "\n".join([header, separator, *body])


def _escape_md(value: str) -> str:
    return value.replace("\n", " ").replace("|", "\\|")
