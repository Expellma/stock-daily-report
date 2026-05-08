"""ChatGPT-powered analysis for local PDF financial reports."""

from __future__ import annotations

import base64
from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import urllib.error
import urllib.request

from .models import PdfReportAnalysis

OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
DEFAULT_OPENAI_MODEL = "gpt-4.1-mini"
MAX_PDF_FILES = 4
MAX_PDF_BYTES = 25 * 1024 * 1024


class ChatGPTAnalysisError(RuntimeError):
    """Raised when ChatGPT analysis cannot be completed."""


def discover_pdf_reports(report_dir: Path) -> list[Path]:
    """Return local PDF report files sorted by filename."""

    if not report_dir.exists():
        raise FileNotFoundError(f"本地财报目录不存在：{report_dir}")
    if not report_dir.is_dir():
        raise NotADirectoryError(f"本地财报路径不是目录：{report_dir}")
    return sorted(
        path
        for path in report_dir.iterdir()
        if path.is_file() and path.suffix.lower() == ".pdf"
    )


def analyze_pdf_reports_with_chatgpt(
    report_dir: Path,
    symbol: str,
    name: str | None = None,
    thesis: str = "",
    model: str | None = None,
    api_key: str | None = None,
) -> PdfReportAnalysis:
    """Analyze local PDF financial reports with ChatGPT and normalize poster fields."""

    pdf_files = discover_pdf_reports(report_dir)
    if not pdf_files:
        raise FileNotFoundError(f"目录中未发现 PDF 财报文件：{report_dir}")
    selected_files = pdf_files[:MAX_PDF_FILES]
    raw = _call_openai_responses(
        selected_files,
        symbol=symbol,
        name=name,
        thesis=thesis,
        model=model or os.getenv("OPENAI_MODEL") or DEFAULT_OPENAI_MODEL,
        api_key=api_key or os.getenv("OPENAI_API_KEY"),
    )
    return _analysis_from_payload(
        raw,
        symbol=symbol,
        name=name,
        thesis=thesis,
        report_dir=report_dir,
        files=selected_files,
        model=model or os.getenv("OPENAI_MODEL") or DEFAULT_OPENAI_MODEL,
    )


def write_pdf_report_analysis_json(
    analysis: PdfReportAnalysis, output_dir: Path
) -> Path:
    """Persist normalized ChatGPT PDF analysis for audit and reuse."""

    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{analysis.symbol.lower()}_pdf_report_analysis.json"
    payload = asdict(analysis)
    payload["generated_at"] = analysis.generated_at.isoformat()
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _call_openai_responses(
    pdf_files: list[Path],
    *,
    symbol: str,
    name: str | None,
    thesis: str,
    model: str,
    api_key: str | None,
) -> dict:
    if not api_key:
        raise ChatGPTAnalysisError(
            "未设置 OPENAI_API_KEY，无法调用 ChatGPT 分析 PDF 财报。"
        )

    content: list[dict[str, str]] = [
        {"type": "input_text", "text": _analysis_prompt(symbol, name, thesis)}
    ]
    for path in pdf_files:
        data = path.read_bytes()
        if len(data) > MAX_PDF_BYTES:
            raise ChatGPTAnalysisError(
                f"PDF 文件过大（>{MAX_PDF_BYTES // 1024 // 1024}MB）：{path}"
            )
        content.append(
            {
                "type": "input_file",
                "filename": path.name,
                "file_data": f"data:application/pdf;base64,{base64.b64encode(data).decode('ascii')}",
            }
        )

    request_body = {
        "model": model,
        "input": [{"role": "user", "content": content}],
        "temperature": 0.2,
    }
    request = urllib.request.Request(
        OPENAI_RESPONSES_URL,
        data=json.dumps(request_body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ChatGPTAnalysisError(
            f"OpenAI Responses API 请求失败：HTTP {exc.code} {detail}"
        ) from exc
    except urllib.error.URLError as exc:
        raise ChatGPTAnalysisError(f"OpenAI Responses API 网络失败：{exc}") from exc

    output_text = _extract_response_text(payload)
    if not output_text:
        raise ChatGPTAnalysisError("ChatGPT 未返回可解析的文本结果。")
    return _parse_json_object(output_text)


def _analysis_prompt(symbol: str, name: str | None, thesis: str) -> str:
    return f"""
你是买方投研分析师。请阅读随附本地 PDF 财报文件，提取适合放入一张中文投研海报的关键信息。
标的：{name or symbol}（{symbol}）
投资主线：{thesis or '未提供，请基于财报自行归纳'}

只输出一个 JSON 对象，不要输出 Markdown 代码块。字段必须包括：
- company_name: 公司名
- period: 财报期间或年份
- title: 18 字以内海报标题
- subtitle: 32 字以内副标题
- verdict: 40 字以内核心结论
- revenue: 营收摘要，含同比/趋势，未知写 N/A
- profit: 利润摘要，含同比/趋势，未知写 N/A
- cash_flow: 现金流摘要，未知写 N/A
- margins: 毛利率/净利率/费用率摘要，未知写 N/A
- growth_drivers: 3 到 5 个增长动因字符串
- risks: 3 到 5 个主要风险字符串
- poster_bullets: 4 到 6 个适合海报展示的短句，每句不超过 28 字
- sources: 逐条列出引用的 PDF 文件名/页码/章节；无法识别页码也要列文件名
- disclaimer: 风险提示
要求：数字要保留原单位；不要编造财报未披露的信息；无法确认时写“待核验”。
""".strip()


def _extract_response_text(payload: dict) -> str:
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"]
    parts: list[str] = []
    for item in payload.get("output", []):
        for content in item.get("content", []):
            if content.get("type") in {"output_text", "text"} and isinstance(
                content.get("text"), str
            ):
                parts.append(content["text"])
    return "\n".join(parts).strip()


def _parse_json_object(text: str) -> dict:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.lower().startswith("json"):
            stripped = stripped[4:].strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ChatGPTAnalysisError("ChatGPT 输出不是 JSON 对象。")
        return json.loads(stripped[start : end + 1])


def _analysis_from_payload(
    payload: dict,
    *,
    symbol: str,
    name: str | None,
    thesis: str,
    report_dir: Path,
    files: list[Path],
    model: str,
) -> PdfReportAnalysis:
    return PdfReportAnalysis(
        generated_at=datetime.now(timezone.utc),
        symbol=symbol.strip().upper(),
        company_name=str(payload.get("company_name") or name or symbol),
        period=str(payload.get("period") or "待核验"),
        title=str(payload.get("title") or f"{name or symbol} 财报速览"),
        subtitle=str(payload.get("subtitle") or thesis or "ChatGPT 本地财报解析"),
        verdict=str(payload.get("verdict") or "待核验"),
        revenue=str(payload.get("revenue") or "N/A"),
        profit=str(payload.get("profit") or "N/A"),
        cash_flow=str(payload.get("cash_flow") or "N/A"),
        margins=str(payload.get("margins") or "N/A"),
        growth_drivers=_string_list(payload.get("growth_drivers"), limit=5),
        risks=_string_list(payload.get("risks"), limit=5),
        poster_bullets=_string_list(payload.get("poster_bullets"), limit=6),
        sources=_string_list(payload.get("sources"), limit=8),
        disclaimer=str(payload.get("disclaimer") or "仅供研究参考，不构成投资建议。"),
        report_dir=str(report_dir),
        files=[path.name for path in files],
        model=model,
    )


def _string_list(value: object, *, limit: int) -> list[str]:
    if isinstance(value, list):
        items = [str(item).strip() for item in value if str(item).strip()]
    elif isinstance(value, str) and value.strip():
        items = [value.strip()]
    else:
        items = []
    return items[:limit]
