"""Fisher growth-investing fundamental analysis and Markdown rendering."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
import textwrap

from .config import Settings
from .data_sources import (
    fetch_cn_company_profile,
    fetch_cn_filings,
    fetch_cn_fundamentals,
    fetch_cn_news,
    fetch_cn_quote,
    fetch_company_profile,
    fetch_earnings,
    fetch_fundamentals,
    fetch_news,
    fetch_quote,
    fetch_sec_fundamentals,
    is_cn_symbol,
)
from .models import (
    AnnualReportEvidence,
    AnnualReportEvidenceItem,
    AnnualReportFile,
    CompanyProfile,
    FisherAnalysis,
    FisherCriterion,
    FundamentalMetric,
    FundamentalSnapshot,
    NewsItem,
    Quote,
    SecFactPoint,
    SecFundamentalData,
    Security,
)
from .report import output_dir_for

INNOVATION_KEYWORDS = frozenset(
    {
        "ai",
        "cloud",
        "chip",
        "platform",
        "patent",
        "launch",
        "partnership",
        "r&d",
        "research",
        "研发投入",
        "研发费用",
        "核心技术",
        "专利",
        "新产品",
        "技术平台",
    }
)

SALES_MARKET_KEYWORDS = frozenset(
    {
        "market share",
        "channel",
        "dealer",
        "customer expansion",
        "order",
        "市场份额",
        "渠道",
        "经销商",
        "客户拓展",
        "订单",
    }
)

MOAT_KEYWORDS = frozenset(
    {
        "brand",
        "scale advantage",
        "network effect",
        "supply chain",
        "customer stickiness",
        "品牌",
        "规模优势",
        "网络效应",
        "供应链",
        "客户粘性",
    }
)

RISK_KEYWORDS = frozenset(
    {
        "lawsuit",
        "probe",
        "fraud",
        "restatement",
        "investigation",
        "行政处罚",
        "诉讼",
        "监管函",
        "监管处罚",
        "会计差错",
        "客户集中度",
        "商誉减值",
    }
)

GOVERNANCE_KEYWORDS = frozenset(
    {
        "sec",
        "governance",
        "related party",
        "关联交易",
        "监管函",
        "会计差错",
        "行政处罚",
        "监管处罚",
    }
)

CASHFLOW_QUALITY_KEYWORDS = frozenset(
    {
        "operating cash flow",
        "accounts receivable",
        "inventory",
        "gross margin",
        "net margin",
        "经营现金流",
        "应收账款",
        "存货",
        "毛利率",
        "净利率",
    }
)

ANNUAL_REPORT_KEYWORDS = tuple(
    sorted(
        INNOVATION_KEYWORDS
        | SALES_MARKET_KEYWORDS
        | MOAT_KEYWORDS
        | RISK_KEYWORDS
        | GOVERNANCE_KEYWORDS
        | CASHFLOW_QUALITY_KEYWORDS,
        key=len,
        reverse=True,
    )
)
SUPPORTED_ANNUAL_REPORT_SUFFIXES = {".txt", ".md", ".pdf"}

DEFAULT_MARKDOWN_POSTER_TEMPLATE = Path("input/templates/财报总结统一模板.md")


FINANCIAL_QUALITY_NEGATIVE_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"经营现金流[^。；;，,]{0,12}(?:为负|转负|下降|减少|恶化)",
        r"(?:negative|declining|weak)\s+operating cash flow",
        r"应收账款[^。；;，,]{0,12}(?:大幅)?(?:增长|增加|高企)",
        r"存货[^。；;，,]{0,12}(?:大幅)?(?:增长|增加|积压|高企)",
        r"毛利率[^。；;，,]{0,12}(?:下降|下滑|承压)",
        r"净利率[^。；;，,]{0,12}(?:下降|下滑|承压)",
    )
)

FINANCIAL_QUALITY_POSITIVE_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"经营现金流[^。；;，,]{0,12}(?:为正|改善|增长|增加)",
        r"(?:positive|improving|strong)\s+operating cash flow",
        r"毛利率[^。；;，,]{0,12}(?:提升|提高|改善|增长)",
        r"净利率[^。；;，,]{0,12}(?:提升|提高|改善|增长)",
    )
)

FISHER_FRAMEWORK: tuple[tuple[str, str], ...] = (
    (
        "产品/服务是否拥有足够大的长期市场空间？",
        "看公司主营业务、行业位置与增长新闻，判断 TAM 是否仍可扩张。",
    ),
    (
        "管理层是否有持续开发新产品和新流程的决心？",
        "关注新品、研发、平台扩展、并购整合和长期战略投入。",
    ),
    (
        "研发投入相对规模是否有效？",
        "公开数据有限时，以创新新闻、产品迭代和技术壁垒作为替代线索。",
    ),
    ("销售组织是否足够强？", "用收入增长、客户/渠道新闻和订单动能评估商业化能力。"),
    (
        "利润率是否具有吸引力？",
        "用毛利率、营业利润率和净利率观察产品差异化与经营杠杆。",
    ),
    ("公司是否在主动维护或提升利润率？", "关注成本控制、定价权、规模效应和费用纪律。"),
    (
        "劳资/人才关系是否健康？",
        "公开数据有限时，记录为待调研，并提示通过员工口碑、流失率验证。",
    ),
    ("高管关系和治理氛围是否健康？", "观察管理层稳定性、战略一致性和重大争议。"),
    ("管理层深度是否足够？", "判断公司是否过度依赖单一人物，以及关键业务是否有梯队。"),
    ("成本分析和会计控制是否可靠？", "用现金流、利润率和财务杠杆交叉验证会计质量。"),
    (
        "是否拥有同行难以复制的业务特征？",
        "寻找网络效应、规模优势、品牌、专利、生态或供应链壁垒。",
    ),
    ("公司是否以长期利润为导向？", "结合投资主线、资本开支、回购/分红和研发投入线索。"),
    ("未来融资是否会显著稀释股东？", "以自由现金流、负债/权益和市值融资能力判断。"),
    (
        "管理层在顺境和逆境中是否坦诚？",
        "需要会议纪要/股东信验证；报告中列出需要进一步核验的问题。",
    ),
    ("管理层诚信是否经得起检验？", "关注监管、诉讼、会计重述和重大争议新闻。"),
)


def build_fisher_analysis(
    settings: Settings,
    symbol: str,
    name: str | None = None,
    thesis: str = "",
    annual_report_dir: Path | None = None,
) -> FisherAnalysis:
    """Build a Fisher-style growth fundamental analysis for one symbol."""

    normalized_symbol = symbol.strip().upper()
    errors: list[str] = []
    if is_cn_symbol(normalized_symbol):
        quote = fetch_cn_quote(normalized_symbol, settings.app)
        profile = fetch_cn_company_profile(normalized_symbol, settings.app)
        fundamentals = fetch_cn_fundamentals(normalized_symbol, settings.app)
        earnings = None
        sec_data = fetch_cn_filings(normalized_symbol, settings.app)
        news = fetch_cn_news(
            normalized_symbol,
            settings.app,
            settings.signals.major_keywords,
            settings.app.max_watchlist_news,
        )
    else:
        quote = fetch_quote(normalized_symbol, settings.app)
        profile = fetch_company_profile(normalized_symbol, settings.app)
        fundamentals = fetch_fundamentals(normalized_symbol, settings.app)
        earnings = fetch_earnings(normalized_symbol, settings.app)
        sec_data = fetch_sec_fundamentals(normalized_symbol, settings.app)
        news = fetch_news(
            normalized_symbol,
            settings.app,
            settings.signals.major_keywords,
            settings.app.max_watchlist_news,
        )

    if quote.error:
        errors.append(f"{normalized_symbol} quote: {quote.error}")
    if profile.error:
        errors.append(f"{normalized_symbol} profile: {profile.error}")
    if fundamentals.error:
        errors.append(f"{normalized_symbol} fundamentals: {fundamentals.error}")
    if earnings and earnings.error:
        errors.append(f"{normalized_symbol} earnings: {earnings.error}")
    if sec_data.error:
        errors.append(
            f"{normalized_symbol} filings/fundamental filings: {sec_data.error}"
        )
    errors.extend(
        f"{item.symbol} news: {item.title}" for item in news if item.score < 0
    )

    security = Security(
        symbol=normalized_symbol,
        name=name or profile.name or normalized_symbol,
        thesis=thesis,
    )
    resolved_report_dir = annual_report_dir or resolve_local_annual_report_dir(
        normalized_symbol, name or profile.name
    )
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
        criteria=_score_fisher_criteria(
            profile.summary,
            fundamentals.metrics,
            clean_news,
            sec_data,
            annual_report_evidence,
        ),
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


def build_fisher_analysis_from_markdown_reports(
    report_dir: Path,
    symbol: str,
    name: str | None = None,
    thesis: str = "",
) -> FisherAnalysis:
    """Build an offline Fisher analysis from local ChatGPT Markdown report files.

    This path intentionally avoids live market-data and LLM API calls. It treats
    Markdown files in ``report_dir`` as already-reviewed financial-report notes,
    extracts keyword evidence from them, and scores the Fisher framework from
    that evidence only.
    """

    normalized_symbol = symbol.upper()
    company_name = name or normalized_symbol
    evidence = load_local_annual_reports(report_dir)
    local_source = "本地 ChatGPT Markdown 财报分析"
    return FisherAnalysis(
        generated_at=datetime.now(timezone.utc),
        security=Security(normalized_symbol, company_name, thesis),
        quote=Quote(normalized_symbol, source=local_source),
        profile=CompanyProfile(
            normalized_symbol,
            company_name,
            source=local_source,
            summary=_combined_markdown_summary(evidence),
        ),
        fundamentals=FundamentalSnapshot(normalized_symbol, source=local_source),
        criteria=_score_fisher_criteria(
            "",
            [],
            [],
            SecFundamentalData(normalized_symbol, source=local_source),
            evidence,
        ),
        sec_data=SecFundamentalData(normalized_symbol, source=local_source),
        annual_report_evidence=evidence,
        errors=evidence.warnings.copy(),
    )


def write_fisher_markdown_poster(
    analysis: FisherAnalysis, output_dir: Path, template_path: Path | None = None
) -> Path:
    """Persist a Markdown poster for an offline Fisher analysis.

    When ``template_path`` is provided, the poster is rendered by reading that
    Markdown template and filling supported ``{{...}}`` placeholders. Without a
    template path this keeps the legacy concise Fisher poster renderer.
    """

    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{analysis.security.symbol.lower()}_fisher_poster.md"
    if template_path is None:
        markdown = render_fisher_markdown_poster(analysis)
    else:
        markdown = render_fisher_markdown_poster_from_template(
            analysis, read_markdown_poster_template(template_path)
        )
    path.write_text(markdown, encoding="utf-8")
    return path


def read_markdown_poster_template(template_path: Path) -> str:
    """Read a Markdown poster template from disk with a clear error message."""

    if not template_path.exists():
        raise FileNotFoundError(f"Markdown 海报模板不存在：{template_path}")
    if not template_path.is_file():
        raise IsADirectoryError(f"Markdown 海报模板不是文件：{template_path}")
    return template_path.read_text(encoding="utf-8")


def resolve_local_annual_report_dir(
    symbol: str,
    name: str | None = None,
    input_root: Path | None = None,
) -> Path:
    """Resolve the default local annual-report directory under project input/.

    Matching is case-insensitive so a symbol such as NVDA can load files from
    input/nvda, input/NVDA, or any other casing used by the local directory.
    """

    root = input_root or Path.cwd() / "input"
    candidates = [candidate for candidate in (name, symbol) if candidate]
    matched = _case_insensitive_child_dir(root, candidates)
    if matched:
        return matched
    fallback_name = candidates[0] if candidates else symbol
    return root / fallback_name


def _case_insensitive_child_dir(root: Path, names: list[str]) -> Path | None:
    wanted = {name.strip().casefold() for name in names if name.strip()}
    if not wanted or not root.is_dir():
        return None
    for child in root.iterdir():
        if child.is_dir() and child.name.casefold() in wanted:
            return child
    return None


def load_local_annual_reports(report_dir: Path) -> AnnualReportEvidence:
    """Load local annual-report snippets without blocking report generation."""

    evidence = AnnualReportEvidence(directory=str(report_dir))
    if not report_dir.exists():
        evidence.warnings.append(f"本地年报目录不存在：{report_dir}")
        return evidence
    if not report_dir.is_dir():
        evidence.warnings.append(f"本地年报路径不是目录：{report_dir}")
        return evidence

    files = sorted(
        path
        for path in report_dir.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_ANNUAL_REPORT_SUFFIXES
    )
    if not files:
        evidence.warnings.append(f"目录中未发现 .txt/.md/.pdf 年报文件：{report_dir}")
        return evidence

    for path in files:
        display_path = str(path)
        if path.suffix.lower() == ".pdf":
            message = "暂不支持直接解析 PDF，请转换为 .txt 或 .md 后重试。"
            evidence.files.append(
                AnnualReportFile(display_path, "unsupported", message)
            )
            evidence.warnings.append(f"{path.name}: {message}")
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                text = path.read_text(encoding="gb18030")
            except OSError as exc:
                message = f"读取失败：{exc}"
                evidence.files.append(AnnualReportFile(display_path, "error", message))
                evidence.warnings.append(f"{path.name}: {message}")
                continue
            except UnicodeDecodeError:
                message = "读取失败：无法用 UTF-8 或 GB18030 解码。"
                evidence.files.append(AnnualReportFile(display_path, "error", message))
                evidence.warnings.append(f"{path.name}: {message}")
                continue
        except OSError as exc:
            message = f"读取失败：{exc}"
            evidence.files.append(AnnualReportFile(display_path, "error", message))
            evidence.warnings.append(f"{path.name}: {message}")
            continue

        if not text.strip():
            message = "文件为空，未提取证据。"
            evidence.files.append(AnnualReportFile(display_path, "empty", message))
            evidence.warnings.append(f"{path.name}: {message}")
            continue

        items = _extract_annual_report_evidence(text, path.name)
        evidence.items.extend(items)
        evidence.files.append(
            AnnualReportFile(
                display_path, "loaded", f"提取 {len(items)} 条关键词证据。"
            )
        )
    return evidence


def _extract_annual_report_evidence(
    text: str, source_file: str
) -> list[AnnualReportEvidenceItem]:
    items: list[AnnualReportEvidenceItem] = []
    seen: set[tuple[str, str]] = set()
    for keyword in ANNUAL_REPORT_KEYWORDS:
        for match in re.finditer(re.escape(keyword), text, re.IGNORECASE):
            excerpt = _keyword_evidence_excerpt(text, match.start(), match.end())
            key = (keyword, excerpt)
            if key in seen:
                continue
            seen.add(key)
            items.append(AnnualReportEvidenceItem(keyword, excerpt, source_file))
            if len(items) >= 30:
                return items
    return items


def _keyword_evidence_excerpt(text: str, start: int, end: int) -> str:
    left_boundaries = [
        text.rfind(mark, 0, start) for mark in ("。", "！", "？", "\n", ";", "；")
    ]
    right_boundaries = [
        pos
        for pos in (
            text.find(mark, end) for mark in ("。", "！", "？", "\n", ";", "；")
        )
        if pos != -1
    ]
    left = (
        max(left_boundaries) + 1 if max(left_boundaries) != -1 else max(0, start - 60)
    )
    right = min(right_boundaries) + 1 if right_boundaries else min(len(text), end + 80)
    return textwrap.shorten(
        " ".join(text[left:right].split()), width=180, placeholder="..."
    )


def render_fisher_markdown(analysis: FisherAnalysis) -> str:
    """Render an elegant Markdown report that is easy to browse in GitHub or Notion."""

    score = _overall_score(analysis.criteria)
    thesis = (
        analysis.security.thesis
        or "未提供自定义投资主线；以下结论仅基于公开行情、基本面与新闻线索。"
    )
    sections = [
        f"# {analysis.security.name}（{analysis.security.symbol}）费雪成长投资基本面分析",
        "",
        f"> 生成时间：{analysis.generated_at.isoformat()} · 框架：Philip Fisher 15 个成长股问题 · {analysis.sec_data.source or '公开披露'}",
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
        f"## 🏛️ {_filing_section_title(analysis.sec_data)}",
        "",
        _sec_filings_table(analysis.sec_data),
        "",
        "## 📚 本地年报文件分析",
        "",
        _annual_report_section(analysis.annual_report_evidence),
        "",
        "### 📈 关键数据图标",
        "",
        _sec_key_data_panel(analysis.sec_data),
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


def render_fisher_markdown_poster(analysis: FisherAnalysis) -> str:
    """Render a concise Markdown poster from offline Fisher analysis results."""

    score = _overall_score(analysis.criteria)
    source_files = [file.path for file in analysis.annual_report_evidence.files]
    source_line = "、".join(Path(path).name for path in source_files) or "未发现可读取文件"
    top_positive = [item for item in analysis.criteria if (item.score or 0) >= 4]
    top_risk = [item for item in analysis.criteria if (item.score or 0) <= 2]
    evidence_items = analysis.annual_report_evidence.items[:8]
    thesis = analysis.security.thesis or "基于本地 Markdown 财报分析内容进行费雪框架初筛。"

    sections = [
        f"# {analysis.security.name}（{analysis.security.symbol}）费雪分析 Markdown 海报",
        "",
        f"> 生成时间：{analysis.generated_at.isoformat()} · 输入：{source_line} · 数据源：本地 Markdown（不调用 GPT/API）",
        "",
        "## 🧭 海报结论",
        "",
        f"- **综合评分：{score}/5**",
        f"- **投资主线：** {thesis}",
        f"- **一句话判断：** {_summary_callout(score).lstrip('> ')}",
        "",
        "## ✅ 费雪强项信号",
        "",
        _poster_criteria_bullets(
            top_positive,
            fallback="暂未在 Markdown 中提取到明显强项；建议补充更完整的财报分析结论。",
        ),
        "",
        "## ⚠️ 主要风险/待核验",
        "",
        _poster_criteria_bullets(
            top_risk,
            fallback="暂未在 Markdown 中提取到显著风险；仍需核验管理层、竞争格局与现金流质量。",
        ),
        "",
        "## 🔎 来自输入 Markdown 的关键证据",
        "",
        _poster_evidence_bullets(evidence_items),
        "",
        "## 🐟 费雪 15 问评分矩阵",
        "",
        _criteria_table(analysis.criteria),
        "",
        "## 📁 输入文件读取状态",
        "",
        _annual_report_section(analysis.annual_report_evidence),
        "",
        "---",
        "仅供研究参考，不构成投资建议；本海报只基于目录内 Markdown 内容生成，未联网抓取行情或重新调用 GPT。",
        "",
    ]
    return "\n".join(sections)


def render_fisher_markdown_poster_from_template(
    analysis: FisherAnalysis, template_text: str
) -> str:
    """Render the offline Markdown poster by filling a user-provided template."""

    replacements = _markdown_poster_template_replacements(analysis)

    def replace_placeholder(match: re.Match[str]) -> str:
        key = match.group(1).strip()
        if key in replacements:
            return replacements[key]
        return _default_template_placeholder_value(key)

    return re.sub(r"\{\{([^{}]*)\}\}", replace_placeholder, template_text)


def _markdown_poster_template_replacements(analysis: FisherAnalysis) -> dict[str, str]:
    score = _overall_score(analysis.criteria)
    source_files = [file.path for file in analysis.annual_report_evidence.files]
    source_line = "、".join(Path(path).name for path in source_files) or "未发现可读取文件"
    summary = _summary_callout(score).lstrip("> ")
    evidence_texts = [item.excerpt for item in analysis.annual_report_evidence.items]
    risk_criteria = [item for item in analysis.criteria if (item.score or 0) <= 2]
    positive_criteria = [item for item in analysis.criteria if (item.score or 0) >= 4]
    generated_date = analysis.generated_at.strftime("%Y-%m-%d")
    generated_year = analysis.generated_at.strftime("%Y")

    return {
        "公司名称": analysis.security.name,
        "Ticker": analysis.security.symbol,
        "财报期间": "待核验",
        "10-K / 10-Q / 年报 / 季报 / 财报新闻稿": "本地 Markdown 财报分析",
        "FY / Q": "待核验",
        "年份": generated_year,
        "日期": generated_date,
        "文件名 / 公司公告 / 10-K / 10-Q": source_line,
        (
            "超预期 / 符合预期 / 低于预期 / 质量改善 / 质量恶化 / "
            "增长放缓但现金流强 / 增长强劲但估值透支"
        ): _template_quality_label(score),
        "强化 / 维持 / 边际转弱 / 被破坏 / 需观察": _template_logic_status(score),
        (
            "收入驱动 / 利润率驱动 / 现金流驱动 / "
            "非经常性收益驱动 / 周期修复驱动"
        ): _template_main_driver(evidence_texts),
        "本期最重要的基本面变化": _first_non_empty(evidence_texts, summary),
        "关键风险或变量": _criteria_template_summary(
            risk_criteria,
            "未在输入 Markdown 中提取到明确风险，仍需核验竞争、现金流与治理。",
        ),
        "收入增速与趋势": _keyword_template_summary(
            evidence_texts, ("收入", "营收", "revenue")
        ),
        "毛利率 / 营业利润率变化": _keyword_template_summary(
            evidence_texts, ("毛利率", "营业利润率", "gross margin", "margin")
        ),
        "经营现金流 / FCF 表现": _keyword_template_summary(
            evidence_texts, ("经营现金流", "自由现金流", "cash flow", "fcf")
        ),
        "现金、债务、流动性": _keyword_template_summary(
            evidence_texts, ("现金", "债务", "流动性", "debt", "liquidity")
        ),
        "回购、分红、并购、再投资": _keyword_template_summary(
            evidence_texts,
            ("回购", "分红", "并购", "再投资", "repurchase", "dividend"),
        ),
        "管理层指引或展望": _keyword_template_summary(
            evidence_texts, ("指引", "展望", "guidance", "outlook")
        ),
        "投资逻辑状态": _template_logic_status(score),
        "低估 / 大致合理 / 透支预期 / 明显高估 / 数据不足": "数据不足",
        (
            "观察仓 / 试探仓 / 分批建仓 / 核心仓等待 / 不追价 / 等回撤"
        ): _template_position_suggestion(score),
        "利润表 / 资产负债表 / 现金流量表 / 附注页码": source_line,
        "一句话总结": summary,
        "观察 / 试探 / 分批 / 持有 / 不追 / 等回撤": _template_short_action(score),
        "验证点 1": _criteria_template_summary(
            positive_criteria[:1], "收入增长、利润率和现金流质量是否继续兑现。"
        ),
        "验证点 2": _criteria_template_summary(
            risk_criteria[:1], "主要风险是否在下一期财报中缓解。"
        ),
        "验证点 3": "补充管理层交流、行业数据和估值敏感性分析。",
    }


def _template_quality_label(score: float) -> str:
    if score >= 4:
        return "质量改善"
    if score <= 2:
        return "质量恶化"
    return "符合预期"


def _template_logic_status(score: float) -> str:
    if score >= 4:
        return "强化"
    if score <= 2:
        return "边际转弱"
    return "维持"


def _template_position_suggestion(score: float) -> str:
    if score >= 4:
        return "分批建仓"
    if score <= 2:
        return "不追价"
    return "观察仓"


def _template_short_action(score: float) -> str:
    if score >= 4:
        return "分批"
    if score <= 2:
        return "不追"
    return "观察"


def _template_main_driver(evidence_texts: list[str]) -> str:
    lowered = "\n".join(evidence_texts).lower()
    if any(
        keyword in lowered for keyword in ("经营现金流", "自由现金流", "cash flow", "fcf")
    ):
        return "现金流驱动"
    if any(keyword in lowered for keyword in ("毛利率", "利润率", "margin")):
        return "利润率驱动"
    if any(keyword in lowered for keyword in ("收入", "营收", "revenue")):
        return "收入驱动"
    return "数据不足"


def _criteria_template_summary(criteria: list[FisherCriterion], fallback: str) -> str:
    if not criteria:
        return fallback
    parts = []
    for criterion in criteria[:3]:
        evidence = criterion.evidence[0] if criterion.evidence else "待核验"
        parts.append(f"{criterion.title}（{criterion.score or 'N/A'}/5）：{evidence}")
    return "；".join(parts)


def _keyword_template_summary(texts: list[str], keywords: tuple[str, ...]) -> str:
    for text in texts:
        normalized = text.lower()
        if any(keyword.lower() in normalized for keyword in keywords):
            return text
    return "数据缺失，需回到原始财报或输入 Markdown 补充。"


def _first_non_empty(values: list[str], fallback: str) -> str:
    for value in values:
        if value.strip():
            return value
    return fallback


def _default_template_placeholder_value(key: str) -> str:
    if not key:
        return "待补充"
    if "数据缺失" in key:
        return "数据缺失"
    if "有 / 无" in key:
        return "数据缺失"
    if "高 / 中 / 低" in key or "强 / 中 / 弱" in key:
        return "数据缺失"
    return f"待核验：{key}"


def _score_fisher_criteria(
    summary: str,
    metrics: list[FundamentalMetric],
    news: list[NewsItem],
    sec_data: SecFundamentalData | None = None,
    annual_report_evidence: AnnualReportEvidence | None = None,
) -> list[FisherCriterion]:
    metric_status = {metric.label: metric.status for metric in metrics}
    evidence_sources = _keyword_evidence_sources(summary, news, annual_report_evidence)
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
            sales_hits = _keyword_matches(evidence_sources, SALES_MARKET_KEYWORDS)
            if sales_hits:
                score += 1
                evidence.append(_keyword_evidence_sentence("销售/市场", sales_hits))
        if index in {5, 6, 10}:
            for label in ("毛利率", "营业利润率", "净利率"):
                score += _status_points(metric_status.get(label))
                evidence.append(_metric_sentence(metrics, label))
            for sec_label in ("毛利润", "营业利润", "净利润"):
                score += _change_points(sec_change.get(sec_label))
                evidence.append(_sec_change_sentence(sec_data, sec_label))
        if index == 13:
            score += _status_points(metric_status.get("自由现金流")) + _status_points(
                metric_status.get("负债/权益")
            )
            score += _change_points(sec_change.get("经营现金流"))
            evidence.extend(
                [
                    _metric_sentence(metrics, "自由现金流"),
                    _metric_sentence(metrics, "负债/权益"),
                    _sec_change_sentence(sec_data, "经营现金流"),
                ]
            )
        if index in {2, 3, 12}:
            innovation_hits = _keyword_matches(evidence_sources, INNOVATION_KEYWORDS)
            if innovation_hits:
                score += 1
                evidence.append(
                    _keyword_evidence_sentence("创新/研发", innovation_hits)
                )
        if index == 11:
            moat_hits = _keyword_matches(
                evidence_sources, MOAT_KEYWORDS | INNOVATION_KEYWORDS
            )
            if moat_hits:
                score += 1
                evidence.append(_keyword_evidence_sentence("护城河", moat_hits))
        if index in {5, 6, 10, 13}:
            quality_hits = _keyword_matches(evidence_sources, CASHFLOW_QUALITY_KEYWORDS)
            quality_direction = _financial_quality_direction(evidence_sources)
            if quality_hits:
                score += quality_direction
                evidence.append(
                    _keyword_evidence_sentence(
                        "财务质量", quality_hits, quality_direction
                    )
                )
        if index in {8, 10, 14, 15}:
            risk_hits = _keyword_matches(
                evidence_sources, RISK_KEYWORDS | GOVERNANCE_KEYWORDS
            )
            if risk_hits:
                score -= 1
                evidence.append(_keyword_evidence_sentence("风险/治理", risk_hits, -1))
        if not evidence:
            evidence.append("公开量化数据不足，建议通过年报、电话会和专家访谈补足。")
        score = min(5, max(1, score))
        criteria.append(
            FisherCriterion(
                index, title, question, _assessment_text(score), evidence, score
            )
        )
    return criteria


def _keyword_evidence_sources(
    summary: str,
    news: list[NewsItem],
    annual_report_evidence: AnnualReportEvidence | None = None,
) -> list[tuple[str, str]]:
    sources: list[tuple[str, str]] = []
    if annual_report_evidence:
        sources.extend(
            ("年报来源", item.excerpt) for item in annual_report_evidence.items
        )
    if summary:
        label = "年报来源" if _looks_like_annual_report(summary) else "接口数据"
        sources.append((label, summary))
    for item in news:
        label = (
            "年报来源"
            if _looks_like_annual_report(item.title)
            or _looks_like_annual_report(item.publisher)
            else "新闻标题"
        )
        sources.append((label, item.title))
    return sources


def _looks_like_annual_report(text: str) -> bool:
    normalized = text.lower()
    return any(
        marker in normalized
        for marker in ("年报", "年度报告", "annual report", "10-k", "10-k/a")
    )


def _keyword_matches(
    sources: list[tuple[str, str]], keywords: frozenset[str]
) -> dict[str, list[str]]:
    matches: dict[str, list[str]] = {}
    for source_label, text in sources:
        source_matches = [
            keyword
            for keyword in sorted(keywords, key=len, reverse=True)
            if _keyword_in_text(keyword, text)
        ]
        if source_matches:
            matches.setdefault(source_label, [])
            matches[source_label].extend(
                keyword
                for keyword in source_matches
                if keyword not in matches[source_label]
            )
    return matches


def _keyword_in_text(keyword: str, text: str) -> bool:
    if not keyword:
        return False
    if keyword.isascii():
        return (
            re.search(
                rf"(?<![A-Za-z0-9_]){re.escape(keyword)}(?![A-Za-z0-9_])",
                text,
                re.IGNORECASE,
            )
            is not None
        )
    return keyword.lower() in text.lower()


def _keyword_evidence_sentence(
    category: str, matches: dict[str, list[str]], direction: int = 0
) -> str:
    source_order = {"年报来源": 0, "新闻标题": 1, "接口数据": 2}
    ordered_items = sorted(
        matches.items(), key=lambda item: source_order.get(item[0], 99)
    )
    parts = [
        f"{source}匹配：{', '.join(keywords[:5])}" for source, keywords in ordered_items
    ]
    direction_text = (
        "（偏正面）" if direction > 0 else "（偏负面）" if direction < 0 else ""
    )
    return f"{category}关键词{direction_text}：" + "；".join(parts) + "。"


def _financial_quality_direction(sources: list[tuple[str, str]]) -> int:
    text = " ".join(text for _, text in sources)
    if any(pattern.search(text) for pattern in FINANCIAL_QUALITY_NEGATIVE_PATTERNS):
        return -1
    if any(pattern.search(text) for pattern in FINANCIAL_QUALITY_POSITIVE_PATTERNS):
        return 1
    return 0


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
        source = sec_data.source if sec_data else "公开披露"
        return f"{source} 近一年 {label} 字段暂无可用数据。"
    latest = points[0]
    previous = points[1] if len(points) > 1 else None
    change = _fact_change(latest, previous)
    if change is None:
        return f"{sec_data.source} 最新 {label}为 {_format_fact_value(latest)}（报告期末 {latest.end_date or 'N/A'}）。"
    return f"{sec_data.source} 最新 {label}为 {_format_fact_value(latest)}，较上一可比披露 {_format_change(change)}。"


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
        ("行情来源", q.source or "N/A"),
        ("画像来源", analysis.profile.source or "N/A"),
        ("基本面来源", analysis.fundamentals.source or "N/A"),
        ("公告来源", analysis.sec_data.source or "N/A"),
        ("最新价格", _format_price(q.price, analysis.quote.source)),
        ("日涨跌幅", _format_percent(q.change_percent)),
        (
            "下次财报",
            (
                analysis.earnings.report_date
                if analysis.earnings and analysis.earnings.report_date
                else "N/A"
            ),
        ),
    ]
    if analysis.profile.summary:
        rows.append(
            (
                "业务摘要",
                textwrap.shorten(
                    analysis.profile.summary, width=220, placeholder="..."
                ),
            )
        )
    return _markdown_table(["项目", "内容"], rows)


def _metrics_table(metrics: list[FundamentalMetric]) -> str:
    if not metrics:
        return "> 暂无可用基本面指标。"
    rows = [
        (
            metric.label,
            metric.value,
            _status_badge(metric.status),
            metric.interpretation,
        )
        for metric in metrics
    ]
    return _markdown_table(["指标", "数值", "状态", "解读"], rows)


def _annual_report_section(evidence: AnnualReportEvidence) -> str:
    if not evidence.directory:
        return "> 未配置本地年报目录。"
    lines = [f"本地目录：`{_escape_md(evidence.directory)}`"]
    if evidence.files:
        rows = [
            (Path(item.path).name, item.status, item.message or "-")
            for item in evidence.files
        ]
        lines.extend(
            [
                "",
                "### 文件读取状态",
                "",
                _markdown_table(["文件", "状态", "说明"], rows),
            ]
        )
    if evidence.items:
        rows = [
            (item.keyword, item.source_file, item.excerpt)
            for item in evidence.items[:20]
        ]
        lines.extend(
            [
                "",
                "### 提取证据",
                "",
                _markdown_table(["关键词", "来源文件", "摘录"], rows),
            ]
        )
    else:
        lines.extend(["", "> 未从本地年报文件中提取到关键词证据。"])
    if evidence.warnings:
        lines.extend(
            [
                "",
                "### 提醒",
                "",
                *[f"- {_escape_md(warning)}" for warning in evidence.warnings],
            ]
        )
    return "\n".join(lines)


def _sec_filings_table(sec_data: SecFundamentalData) -> str:
    source = sec_data.source or "公开披露"
    if sec_data.error and not sec_data.filings:
        return f"> {source} 数据获取失败：{_escape_md(sec_data.error)}"
    if not sec_data.filings:
        return f"> 近一年未在 {source} 中找到财报/公告。"
    rows = []
    for filing in sec_data.filings:
        link = f"[打开披露文件]({filing.url})" if filing.url else "N/A"
        rows.append(
            (
                filing.form,
                filing.filing_date or "N/A",
                filing.report_date or "N/A",
                filing.description or filing.primary_document,
                link,
            )
        )
    return _markdown_table(["表格", "提交日", "报告期", "说明", "来源"], rows)


def _sec_key_data_panel(sec_data: SecFundamentalData) -> str:
    if not sec_data.facts:
        return f"> 暂无可图表化的 {sec_data.source or '公开披露'} 关键财务数据。"
    preferred = [
        "收入",
        "毛利润",
        "营业利润",
        "净利润",
        "稀释 EPS",
        "经营现金流",
        "资本开支",
        "研发费用",
        "总资产",
        "总负债",
        "股东权益",
    ]
    rows = []
    for label in preferred:
        points = sec_data.facts.get(label, [])
        if not points:
            continue
        latest = points[0]
        previous = points[1] if len(points) > 1 else None
        change = _fact_change(latest, previous)
        rows.append(
            (
                _metric_icon(label, change),
                label,
                _format_fact_value(latest),
                latest.end_date or latest.filed_date or "N/A",
                _format_change(change),
                _sparkline(points),
            )
        )
    return _markdown_table(
        ["图标", "关键数据", "最新值", "报告期末", "环比/同比线索", "近一年趋势"], rows
    )


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
    return "".join(
        blocks[round((value - low) / (high - low) * (len(blocks) - 1))]
        for value in values
    )


def _criteria_table(criteria: list[FisherCriterion]) -> str:
    rows = []
    for item in criteria:
        rows.append(
            (
                f"{item.number}. {item.title}",
                f"{item.score}/5" if item.score else "N/A",
                item.assessment,
                "<br>".join(item.evidence),
            )
        )
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
    filing_prompt = (
        "阅读最近 2 次 10-K/10-Q"
        if "SEC" in (analysis.sec_data.source or "")
        else "阅读最近 2 期年报/季报与交易所公告"
    )
    return "\n".join(
        [
            f"- {filing_prompt}，拆分收入增长的价格、销量、产品和地区贡献。",
            "- 对照电话会纪要/业绩说明会核验管理层是否持续解释长期投入、利润率改善路径和风险。",
            "- 访谈客户、供应商或渠道伙伴，验证产品差异化与销售组织质量。",
            "- 跟踪竞争对手毛利率、研发强度与新品节奏，确认护城河是否扩大。",
            f"- 围绕“{analysis.security.thesis or analysis.security.name}”建立 3-5 个可证伪的季度观察指标。",
        ]
    )


def _risk_notes(analysis: FisherAnalysis) -> str:
    notes = [
        "- 本报告为自动化初筛，不构成投资建议；评分用于组织尽调优先级，不应单独作为买卖依据。"
    ]
    if analysis.errors:
        notes.append(
            "- 部分数据源返回异常："
            + "; ".join(_escape_md(error) for error in analysis.errors)
        )
    notes.append(
        f"- 披露数据来自 {analysis.sec_data.source or '公开披露页面'}；字段可能因公司披露口径不同而缺失或不可比。"
    )
    notes.append(
        f"- 行情/新闻/基本面公共接口可能滞后或缺失；当前行情来源：{analysis.quote.source or 'N/A'}，关键结论需用公司公告与原始披露文件复核。"
    )
    return "\n".join(notes)


def _combined_markdown_summary(evidence: AnnualReportEvidence) -> str:
    excerpts = [item.excerpt for item in evidence.items[:6]]
    return textwrap.shorten(" ".join(excerpts), width=300, placeholder="...")


def _poster_criteria_bullets(
    criteria: list[FisherCriterion], *, fallback: str
) -> str:
    if not criteria:
        return f"- {fallback}"
    lines: list[str] = []
    for criterion in criteria[:5]:
        evidence = (
            criterion.evidence[0]
            if criterion.evidence
            else "证据不足，需继续核验。"
        )
        lines.append(
            f"- **{criterion.number}. {criterion.title}**："
            f"{criterion.assessment}（{criterion.score}/5）— {evidence}"
        )
    return "\n".join(lines)


def _poster_evidence_bullets(items: list[AnnualReportEvidenceItem]) -> str:
    if not items:
        return (
            "- 未提取到关键词证据；请确认目录中存在非空 .md 文件，"
            "且内容包含财报分析结论。"
        )
    return "\n".join(
        f"- **{item.keyword}**（{item.source_file}）：{item.excerpt}" for item in items
    )


def _summary_callout(score: int) -> str:
    if score >= 4:
        return "> ✅ **初筛结论：** 基本面质量与成长线索较强，可进入深度尽调与估值情景分析。"
    if score >= 3:
        return "> 🟡 **初筛结论：** 存在可研究的成长线索，但仍需补充管理层、竞争格局和估值验证。"
    return (
        "> 🔴 **初筛结论：** 当前公开数据未形成足够强的费雪式成长证据，建议先列入观察。"
    )


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
    return {"positive": "🟢 正面", "negative": "🔴 风险", "neutral": "⚪ 中性"}.get(
        status, "⚪ 中性"
    )


def _filing_section_title(sec_data: SecFundamentalData) -> str:
    source = sec_data.source or "公开披露"
    if "SEC" in source:
        return "SEC EDGAR 近一年财报数据"
    return "大陆公告/年报索引"


def _format_price(value: float | None, source: str = "") -> str:
    if value is None:
        return "N/A"
    currency = (
        "¥" if any(name in source for name in ("东方财富", "腾讯", "大陆")) else "$"
    )
    return f"{currency}{value:,.2f}"


def _format_percent(value: float | None) -> str:
    return "N/A" if value is None else f"{value:+.2f}%"


def _markdown_table(headers: list[str], rows: list[tuple[object, ...]]) -> str:
    header = "| " + " | ".join(headers) + " |"
    separator = "| " + " | ".join("---" for _ in headers) + " |"
    body = [
        "| " + " | ".join(_escape_md(str(cell)) for cell in row) + " |" for row in rows
    ]
    return "\n".join([header, separator, *body])


def _escape_md(value: str) -> str:
    return value.replace("\n", " ").replace("|", "\\|")
