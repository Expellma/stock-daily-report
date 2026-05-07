"""Network data adapters for market data, news, and earnings events.

The adapters intentionally use public, keyless endpoints so the workflow can run
locally without secret management. Each function returns structured errors
instead of raising on transient network failures, allowing the poster job to
finish and leave an auditable JSON artifact.
"""

from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import json
from pathlib import Path
import re
from urllib.error import URLError, HTTPError
from urllib.parse import quote, quote_plus, urlencode
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

from .config import AppConfig
from .models import CompanyProfile, EarningsEvent, FundamentalMetric, FundamentalSnapshot, NewsItem, Quote, SecFactPoint, SecFiling, SecFundamentalData, Security


YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=5d&interval=1d"
YAHOO_RSS_URL = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US"
NASDAQ_EARNINGS_URL = "https://api.nasdaq.com/api/calendar/earnings"
YAHOO_QUOTE_SUMMARY_URL = "https://query2.finance.yahoo.com/v10/finance/quoteSummary/{symbol}?modules={modules}"
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
SEC_ARCHIVES_DOC_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}"

EASTMONEY_QUOTE_URL = "https://push2.eastmoney.com/api/qt/stock/get?secid={secid}&fields=f43,f46,f60,f170,f47,f58,f57"
TENCENT_QUOTE_URL = "https://qt.gtimg.cn/q={tencent_symbol}"
EASTMONEY_PROFILE_URL = "https://emweb.securities.eastmoney.com/PC_HSF10/CompanySurvey/PageAjax?code={market_symbol}"
EASTMONEY_FUNDAMENTALS_URL = "https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/ZYZBAjaxNew?type=1&code={market_symbol}"
EASTMONEY_NEWS_URL = "https://search-api-web.eastmoney.com/search/jsonp?param={param}"
CNINFO_FILINGS_URL = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
SSE_FILINGS_URL = "https://query.sse.com.cn/security/stock/queryCompanyBulletin.do?{query}"
SZSE_FILINGS_URL = "https://www.szse.cn/api/disc/announcement/annList?{query}"

CN_SOURCE_QUOTE = "东方财富/腾讯财经公开行情"
CN_SOURCE_PROFILE = "东方财富 F10 公司资料"
CN_SOURCE_FUNDAMENTALS = "东方财富 F10 主要指标"
CN_SOURCE_NEWS = "东方财富资讯公开搜索"
CN_SOURCE_FILINGS = "巨潮资讯 CNINFO/交易所公告索引"

SEC_FACT_TAGS: dict[str, tuple[str, ...]] = {
    "收入": ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"),
    "毛利润": ("GrossProfit",),
    "营业利润": ("OperatingIncomeLoss",),
    "净利润": ("NetIncomeLoss",),
    "稀释 EPS": ("EarningsPerShareDiluted",),
    "经营现金流": ("NetCashProvidedByUsedInOperatingActivities",),
    "资本开支": ("PaymentsToAcquirePropertyPlantAndEquipment",),
    "研发费用": ("ResearchAndDevelopmentExpense",),
    "总资产": ("Assets",),
    "总负债": ("Liabilities",),
    "股东权益": ("StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"),
}


def read_securities(path: Path, include_thesis: bool = True) -> list[Security]:
    """Read a symbol CSV into normalized securities."""

    with path.open(newline="", encoding="utf-8") as fh:
        rows = csv.DictReader(fh)
        securities: list[Security] = []
        for row in rows:
            symbol = row.get("symbol", "").strip().upper()
            if not symbol:
                continue
            securities.append(
                Security(
                    symbol=symbol,
                    name=row.get("name", symbol).strip() or symbol,
                    thesis=(row.get("thesis", "").strip() if include_thesis else ""),
                )
            )
    return securities


def _get_text(url: str, app_config: AppConfig) -> str:
    request = Request(url, headers={"User-Agent": app_config.user_agent})
    with urlopen(request, timeout=app_config.request_timeout_seconds) as response:  # noqa: S310 - configured public URLs only
        return response.read().decode("utf-8", errors="replace")



def fetch_cn_quote(symbol: str, app_config: AppConfig) -> Quote:
    """Fetch A-share quote data from Mainland-accessible public endpoints."""

    normalized_symbol = _normalize_cn_symbol(symbol)
    secid = _eastmoney_secid(normalized_symbol)
    if not secid:
        return Quote(symbol=normalized_symbol, source=CN_SOURCE_QUOTE, error="unsupported A-share symbol format")
    try:
        raw = _get_text(EASTMONEY_QUOTE_URL.format(secid=secid), app_config)
        payload = json.loads(raw)
        data = payload.get("data", {}) if isinstance(payload, dict) else {}
        if not data:
            raise ValueError("empty Eastmoney quote payload")
        price = _scaled_cn_price(data.get("f43"))
        previous_close = _scaled_cn_price(data.get("f60"))
        change_percent = _scaled_cn_percent(data.get("f170"))
        if change_percent is None and price is not None and previous_close not in (None, 0):
            change_percent = (price - previous_close) / previous_close * 100
        return Quote(
            symbol=normalized_symbol,
            price=price,
            previous_close=previous_close,
            change_percent=change_percent,
            volume=_int_or_none(data.get("f47")),
            source=CN_SOURCE_QUOTE,
        )
    except (KeyError, ValueError, TypeError, URLError, HTTPError) as eastmoney_exc:
        return _fetch_tencent_cn_quote(normalized_symbol, app_config, str(eastmoney_exc))


def fetch_cn_company_profile(symbol: str, app_config: AppConfig) -> CompanyProfile:
    """Fetch A-share company profile data from Eastmoney F10."""

    normalized_symbol = _normalize_cn_symbol(symbol)
    market_symbol = _eastmoney_market_symbol(normalized_symbol)
    if not market_symbol:
        return CompanyProfile(symbol=normalized_symbol, name=normalized_symbol, source=CN_SOURCE_PROFILE, error="unsupported A-share symbol format")
    try:
        payload = json.loads(_get_text(EASTMONEY_PROFILE_URL.format(market_symbol=market_symbol), app_config))
        profile = _first_dict(payload, "jbzl", "JBZL", "CompanySurvey", "data") or payload
        name = _pick_text(profile, "SECURITY_NAME_ABBR", "SECURITY_NAME", "ORG_NAME", "股票简称", "公司名称") or normalized_symbol
        summary_parts = [
            _pick_text(profile, "BUSINESS_SCOPE", "经营范围"),
            _pick_text(profile, "MAIN_BUSINESS", "主营业务"),
            _pick_text(profile, "ORG_PROFILE", "公司简介"),
        ]
        return CompanyProfile(
            symbol=normalized_symbol,
            name=name,
            sector=_pick_text(profile, "INDUSTRYCSRC1", "INDUSTRY", "所属行业") or "",
            industry=_pick_text(profile, "INDUSTRYCSRC2", "主营行业") or "",
            website=_pick_text(profile, "WWW_ADDRESS", "WEB_SITE", "公司网址") or "",
            summary=" ".join(part for part in summary_parts if part),
            source=CN_SOURCE_PROFILE,
        )
    except (ValueError, TypeError, URLError, HTTPError) as exc:
        return CompanyProfile(symbol=normalized_symbol, name=normalized_symbol, source=CN_SOURCE_PROFILE, error=str(exc))


def fetch_cn_fundamentals(symbol: str, app_config: AppConfig) -> FundamentalSnapshot:
    """Fetch A-share key metrics from Eastmoney F10 financial-analysis pages."""

    normalized_symbol = _normalize_cn_symbol(symbol)
    market_symbol = _eastmoney_market_symbol(normalized_symbol)
    if not market_symbol:
        return FundamentalSnapshot(symbol=normalized_symbol, source=CN_SOURCE_FUNDAMENTALS, error="unsupported A-share symbol format")
    try:
        payload = json.loads(_get_text(EASTMONEY_FUNDAMENTALS_URL.format(market_symbol=market_symbol), app_config))
        rows = _extract_cn_fundamental_rows(payload)
        latest = rows[0] if rows else {}
        previous = rows[1] if len(rows) > 1 else {}
        revenue_growth = _ratio_change(_number_from_keys(latest, "TOTAL_OPERATE_INCOME", "营业总收入", "营业收入"), _number_from_keys(previous, "TOTAL_OPERATE_INCOME", "营业总收入", "营业收入"))
        gross_margin = _number_from_keys(latest, "GROSS_PROFIT_RATIO", "销售毛利率", "毛利率")
        operating_margin = _number_from_keys(latest, "OPERATE_PROFIT_RATIO", "营业利润率")
        net_margin = _number_from_keys(latest, "NETPROFITRATIO", "销售净利率", "净利率")
        roe = _number_from_keys(latest, "ROE", "JQROE", "净资产收益率")
        debt_to_equity = _number_from_keys(latest, "DEBT_EQUITY_RATIO", "产权比率", "资产负债率")
        metrics = [
            _cn_metric("收入增长", revenue_growth, "费雪框架重视长期可扩展市场和销售成长。", _status_from_percent(revenue_growth, 0.10, 0.0), percent=True),
            _cn_metric("毛利率", gross_margin, "高毛利通常意味着产品差异化、定价权或规模优势。", _status_from_percent(_percent_to_ratio(gross_margin), 0.40, 0.25), percent=True),
            _cn_metric("营业利润率", operating_margin, "衡量管理层将增长转化为经营利润的能力。", _status_from_percent(_percent_to_ratio(operating_margin), 0.20, 0.10), percent=True),
            _cn_metric("净利率", net_margin, "反映成本控制、商业模式质量和周期韧性。", _status_from_percent(_percent_to_ratio(net_margin), 0.15, 0.05), percent=True),
            _cn_metric("ROE", roe, "资本效率越高，越符合成长股复利要求。", _status_from_percent(_percent_to_ratio(roe), 0.15, 0.08), percent=True),
            _cn_metric("负债/权益", debt_to_equity, "财务杠杆过高会削弱长期投入和逆周期能力。", _status_from_inverse(debt_to_equity, 80, 150)),
        ]
        return FundamentalSnapshot(symbol=normalized_symbol, metrics=[item for item in metrics if item.value != "N/A"], source=CN_SOURCE_FUNDAMENTALS)
    except (ValueError, TypeError, URLError, HTTPError) as exc:
        return FundamentalSnapshot(symbol=normalized_symbol, source=CN_SOURCE_FUNDAMENTALS, error=str(exc))


def fetch_cn_news(symbol: str, app_config: AppConfig, keywords: list[str], limit: int) -> list[NewsItem]:
    """Fetch and score A-share news from Mainland-accessible public pages."""

    normalized_symbol = _normalize_cn_symbol(symbol)
    query = normalized_symbol[:6]
    param = quote(json.dumps({"uid":"","keyword":query,"type":["cmsArticleWebOld"],"client":"web","clientType":"web","pageIndex":1,"pageSize":limit}, ensure_ascii=False))
    try:
        raw = _get_text(EASTMONEY_NEWS_URL.format(param=param), app_config)
        payload = _loads_jsonp(raw)
        rows = _find_list(payload, "cmsArticleWebOld", "items", "data")
    except (ValueError, TypeError, URLError, HTTPError) as exc:
        return [NewsItem(symbol=normalized_symbol, title=f"大陆新闻获取失败: {exc}", link="", publisher=CN_SOURCE_NEWS, score=-1)]
    items: list[NewsItem] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = _pick_text(row, "title", "Title", "NEWS_TITLE", "ART_TITLE") or ""
        if not title:
            continue
        link = _pick_text(row, "url", "Url", "NEWS_URL", "ART_URL") or ""
        published = _parse_cn_datetime(_pick_text(row, "date", "showTime", "publishTime", "NOTICE_DATE"))
        items.append(NewsItem(symbol=normalized_symbol, title=title, link=link, publisher=CN_SOURCE_NEWS, published_at=published, score=score_news(title, keywords)))
    return sorted(items, key=lambda item: (item.score, item.published_at or datetime.min.replace(tzinfo=timezone.utc)), reverse=True)[:limit]


def fetch_cn_filings(symbol: str, app_config: AppConfig, lookback_days: int = 365) -> SecFundamentalData:
    """Fetch recent A-share annual-report and announcement indexes from CNINFO/exchanges."""

    normalized_symbol = _normalize_cn_symbol(symbol)
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=lookback_days)
    try:
        query = urlencode({"stock": normalized_symbol[:6], "searchkey": "年报", "pageNum": 1, "pageSize": 30, "column": "sse" if normalized_symbol.endswith(".SH") else "szse", "tabName": "fulltext"})
        payload = json.loads(_get_text(f"{CNINFO_FILINGS_URL}?{query}", app_config))
        announcements = payload.get("announcements", []) if isinstance(payload, dict) else []
        filings = [_cn_announcement_to_filing(item) for item in announcements if isinstance(item, dict)]
        filings = [filing for filing in filings if filing and _is_recent_iso_date(filing.filing_date, cutoff)]
        if filings:
            return SecFundamentalData(symbol=normalized_symbol, filings=sorted(filings, key=lambda filing: filing.filing_date, reverse=True), source=CN_SOURCE_FILINGS)
        exchange_filings = _fetch_exchange_cn_filings(normalized_symbol, app_config, cutoff)
        if exchange_filings:
            return SecFundamentalData(symbol=normalized_symbol, filings=exchange_filings, source=CN_SOURCE_FILINGS)
        return SecFundamentalData(symbol=normalized_symbol, source=CN_SOURCE_FILINGS, error="no annual-report announcements found in lookback window")
    except (ValueError, TypeError, URLError, HTTPError) as exc:
        return SecFundamentalData(symbol=normalized_symbol, source=CN_SOURCE_FILINGS, error=str(exc))

def fetch_quote(symbol: str, app_config: AppConfig) -> Quote:
    """Fetch latest daily quote data from Yahoo's chart endpoint."""

    try:
        raw = _get_text(YAHOO_CHART_URL.format(symbol=quote_plus(symbol)), app_config)
        payload = json.loads(raw)
        result = payload["chart"]["result"][0]
        meta = result["meta"]
        price = _to_float(meta.get("regularMarketPrice"))
        previous_close = _to_float(meta.get("previousClose"))
        change_percent = None
        if price is not None and previous_close not in (None, 0):
            change_percent = (price - previous_close) / previous_close * 100
        volume = _last_number(result.get("indicators", {}).get("quote", [{}])[0].get("volume", []))
        return Quote(symbol=symbol, price=price, previous_close=previous_close, change_percent=change_percent, volume=volume, source="Yahoo Finance chart")
    except (KeyError, IndexError, ValueError, TypeError, URLError, HTTPError) as exc:
        return Quote(symbol=symbol, source="Yahoo Finance chart", error=str(exc))


def fetch_news(symbol: str, app_config: AppConfig, keywords: list[str], limit: int) -> list[NewsItem]:
    """Fetch and score symbol news from Yahoo Finance RSS."""

    try:
        raw = _get_text(YAHOO_RSS_URL.format(symbol=quote_plus(symbol)), app_config)
        root = ET.fromstring(raw)
    except (ET.ParseError, URLError, HTTPError) as exc:
        return [NewsItem(symbol=symbol, title=f"News fetch failed: {exc}", link="", publisher="Yahoo Finance RSS", score=-1)]

    items: list[NewsItem] = []
    for item in root.findall("./channel/item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        published = _parse_rss_date(item.findtext("pubDate"))
        score = score_news(title, keywords)
        if title:
            items.append(NewsItem(symbol=symbol, title=title, link=link, publisher="Yahoo Finance RSS", published_at=published, score=score))
    return sorted(items, key=lambda item: (item.score, item.published_at or datetime.min.replace(tzinfo=timezone.utc)), reverse=True)[:limit]


def fetch_earnings(symbol: str, app_config: AppConfig) -> EarningsEvent:
    """Fetch the nearest earnings event from Nasdaq's public calendar API."""

    query = urlencode({"date": datetime.now(timezone.utc).date().isoformat(), "symbol": symbol})
    try:
        raw = _get_text(f"{NASDAQ_EARNINGS_URL}?{query}", app_config)
        payload = json.loads(raw)
        data = payload.get("data") if isinstance(payload, dict) else None
        rows = data.get("rows", []) if isinstance(data, dict) else []
        if not rows:
            return EarningsEvent(symbol=symbol)
        row = rows[0]
        return EarningsEvent(
            symbol=symbol,
            report_date=_strip_html(row.get("reportDate") or row.get("date")),
            fiscal_quarter=_strip_html(row.get("fiscalQuarterEnding")),
            estimate=_strip_html(row.get("epsForecast")),
        )
    except (ValueError, TypeError, URLError, HTTPError) as exc:
        return EarningsEvent(symbol=symbol, error=str(exc))



def fetch_company_profile(symbol: str, app_config: AppConfig) -> CompanyProfile:
    """Fetch company profile fields suitable for qualitative fundamental analysis."""

    payload, error = _fetch_quote_summary(symbol, app_config, ["assetProfile", "price"])
    if error:
        return CompanyProfile(symbol=symbol, name=symbol, source="Yahoo Finance quoteSummary", error=error)
    result = _quote_summary_result(payload)
    profile = result.get("assetProfile", {}) if isinstance(result, dict) else {}
    price = result.get("price", {}) if isinstance(result, dict) else {}
    name = _display_value(price.get("longName")) or _display_value(price.get("shortName")) or symbol
    return CompanyProfile(
        symbol=symbol,
        name=name,
        sector=_display_value(profile.get("sector")) or "",
        industry=_display_value(profile.get("industry")) or "",
        website=_display_value(profile.get("website")) or "",
        summary=_display_value(profile.get("longBusinessSummary")) or "",
        source="Yahoo Finance quoteSummary",
    )


def fetch_fundamentals(symbol: str, app_config: AppConfig) -> FundamentalSnapshot:
    """Fetch and normalize key fundamentals for Fisher-style growth analysis."""

    modules = ["financialData", "defaultKeyStatistics", "summaryDetail", "price"]
    payload, error = _fetch_quote_summary(symbol, app_config, modules)
    if error:
        return FundamentalSnapshot(symbol=symbol, source="Yahoo Finance quoteSummary", error=error)
    result = _quote_summary_result(payload)
    if not result:
        return FundamentalSnapshot(symbol=symbol, source="Yahoo Finance quoteSummary", error="empty quoteSummary result")

    financial = result.get("financialData", {})
    stats = result.get("defaultKeyStatistics", {})
    summary = result.get("summaryDetail", {})
    price = result.get("price", {})
    metrics = [
        _metric("市值", price.get("marketCap"), "规模与融资能力的粗略代理。", "neutral"),
        _metric("收入增长", financial.get("revenueGrowth"), "费雪框架重视长期可扩展市场和销售成长。", _status_from_percent(financial.get("revenueGrowth"), 0.10, 0.0)),
        _metric("毛利率", financial.get("grossMargins"), "高毛利通常意味着产品差异化、定价权或规模优势。", _status_from_percent(financial.get("grossMargins"), 0.40, 0.25)),
        _metric("营业利润率", financial.get("operatingMargins"), "衡量管理层将增长转化为经营利润的能力。", _status_from_percent(financial.get("operatingMargins"), 0.20, 0.10)),
        _metric("净利率", financial.get("profitMargins"), "反映成本控制、商业模式质量和周期韧性。", _status_from_percent(financial.get("profitMargins"), 0.15, 0.05)),
        _metric("ROE", financial.get("returnOnEquity"), "资本效率越高，越符合成长股复利要求。", _status_from_percent(financial.get("returnOnEquity"), 0.15, 0.08)),
        _metric("自由现金流", financial.get("freeCashflow"), "成长投资需要关注利润是否能沉淀为现金。", "positive" if _raw_number(financial.get("freeCashflow")) and _raw_number(financial.get("freeCashflow")) > 0 else "negative"),
        _metric("负债/权益", financial.get("debtToEquity"), "财务杠杆过高会削弱长期投入和逆周期能力。", _status_from_inverse(financial.get("debtToEquity"), 80, 150)),
        _metric("远期 P/E", summary.get("forwardPE"), "估值不是费雪框架核心，但决定安全边际与预期门槛。", "neutral"),
        _metric("PEG", stats.get("pegRatio"), "以增长校准估值，低于 1 通常更有吸引力。", _status_from_inverse(stats.get("pegRatio"), 1.2, 2.0)),
    ]
    return FundamentalSnapshot(symbol=symbol, metrics=[item for item in metrics if item.value != "N/A"], source="Yahoo Finance quoteSummary")



def _fetch_exchange_cn_filings(symbol: str, app_config: AppConfig, cutoff) -> list[SecFiling]:
    """Best-effort fallback against exchange announcement indexes when CNINFO has no rows."""

    try:
        if symbol.endswith(".SH"):
            query = urlencode({"jsonCallBack": "", "isPagination": "true", "productId": symbol[:6], "securityType": "0101", "reportType2": "DQBG", "pageHelp.pageSize": 20, "pageHelp.pageNo": 1})
            payload = _loads_jsonp(_get_text(SSE_FILINGS_URL.format(query=query), app_config))
        elif symbol.endswith(".SZ"):
            query = urlencode({"random": "", "channelCode": "listedNotice_disc", "pageSize": 20, "pageNum": 1, "stock": symbol[:6], "seDate": ""})
            payload = json.loads(_get_text(SZSE_FILINGS_URL.format(query=query), app_config))
        else:
            return []
    except (ValueError, TypeError, URLError, HTTPError):
        return []
    rows = _find_list(payload, "result", "data", "announcements")
    filings = [_cn_announcement_to_filing(row) for row in rows if isinstance(row, dict)]
    return sorted([filing for filing in filings if filing and _is_recent_iso_date(filing.filing_date, cutoff)], key=lambda filing: filing.filing_date, reverse=True)


def is_cn_symbol(symbol: str) -> bool:
    """Return True for common A-share formats such as 600000.SH, 000001.SZ, or 6 digits."""

    normalized = symbol.strip().upper()
    return bool(re.fullmatch(r"\d{6}(\.(SH|SZ|BJ))?", normalized))


def _normalize_cn_symbol(symbol: str) -> str:
    normalized = symbol.strip().upper()
    if re.fullmatch(r"\d{6}", normalized):
        if normalized.startswith(("5", "6", "9")):
            return f"{normalized}.SH"
        if normalized.startswith(("0", "1", "2", "3")):
            return f"{normalized}.SZ"
        if normalized.startswith(("4", "8")):
            return f"{normalized}.BJ"
    return normalized


def _eastmoney_secid(symbol: str) -> str | None:
    normalized = _normalize_cn_symbol(symbol)
    if normalized.endswith(".SH"):
        return f"1.{normalized[:6]}"
    if normalized.endswith((".SZ", ".BJ")):
        return f"0.{normalized[:6]}"
    return None


def _eastmoney_market_symbol(symbol: str) -> str | None:
    normalized = _normalize_cn_symbol(symbol)
    if normalized.endswith(".SH"):
        return f"SH{normalized[:6]}"
    if normalized.endswith(".SZ"):
        return f"SZ{normalized[:6]}"
    if normalized.endswith(".BJ"):
        return f"BJ{normalized[:6]}"
    return None


def _fetch_tencent_cn_quote(symbol: str, app_config: AppConfig, prior_error: str) -> Quote:
    tencent_symbol = _tencent_symbol(symbol)
    if not tencent_symbol:
        return Quote(symbol=symbol, source=CN_SOURCE_QUOTE, error=prior_error)
    try:
        raw = _get_text(TENCENT_QUOTE_URL.format(tencent_symbol=tencent_symbol), app_config)
        fields = raw.split('="', 1)[1].split('";', 1)[0].split("~")
        price = _to_float(fields[3]) if len(fields) > 3 else None
        previous_close = _to_float(fields[4]) if len(fields) > 4 else None
        change_percent = _to_float(fields[32]) if len(fields) > 32 else None
        volume = _int_or_none(_to_float(fields[6]) * 100 if len(fields) > 6 and _to_float(fields[6]) is not None else None)
        return Quote(symbol=symbol, price=price, previous_close=previous_close, change_percent=change_percent, volume=volume, source=CN_SOURCE_QUOTE)
    except (IndexError, ValueError, TypeError, URLError, HTTPError) as exc:
        return Quote(symbol=symbol, source=CN_SOURCE_QUOTE, error=f"Eastmoney: {prior_error}; Tencent: {exc}")


def _tencent_symbol(symbol: str) -> str | None:
    normalized = _normalize_cn_symbol(symbol)
    if normalized.endswith(".SH"):
        return f"sh{normalized[:6]}"
    if normalized.endswith(".SZ"):
        return f"sz{normalized[:6]}"
    if normalized.endswith(".BJ"):
        return f"bj{normalized[:6]}"
    return None


def _scaled_cn_price(value: object) -> float | None:
    raw = _to_float(value)
    if raw is None or raw <= -100000:
        return None
    return raw / 100


def _scaled_cn_percent(value: object) -> float | None:
    raw = _to_float(value)
    if raw is None or raw <= -100000:
        return None
    return raw / 100


def _first_dict(payload: object, *keys: str) -> dict | None:
    if not isinstance(payload, dict):
        return None
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list) and value and isinstance(value[0], dict):
            return value[0]
        if isinstance(value, dict):
            return value
    for value in payload.values():
        if isinstance(value, list) and value and isinstance(value[0], dict):
            return value[0]
    return None


def _pick_text(mapping: dict, *keys: str) -> str | None:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, "", "--"):
            return str(value).strip()
    return None


def _extract_cn_fundamental_rows(payload: object) -> list[dict]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("data", "zyzb", "ZYZB", "report", "REPORT"):
        value = payload.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
        if isinstance(value, dict):
            nested = _extract_cn_fundamental_rows(value)
            if nested:
                return nested
    for value in payload.values():
        if isinstance(value, list) and value and isinstance(value[0], dict):
            return value
    return []


def _number_from_keys(mapping: dict, *keys: str) -> float | None:
    for key in keys:
        value = mapping.get(key)
        number = _to_float(value)
        if number is not None:
            return number
    return None


def _ratio_change(latest: float | None, previous: float | None) -> float | None:
    if latest is None or previous in (None, 0):
        return None
    return (latest - previous) / abs(previous)


def _percent_to_ratio(value: float | None) -> float | None:
    if value is None:
        return None
    return value / 100 if abs(value) > 1 else value


def _cn_metric(label: str, value: float | None, interpretation: str, status: str, percent: bool = False) -> FundamentalMetric:
    if value is None:
        formatted = "N/A"
    elif percent:
        formatted = f"{value:.2%}" if abs(value) <= 1 else f"{value:.2f}%"
    else:
        formatted = f"{value:,.2f}"
    return FundamentalMetric(label=label, value=formatted, interpretation=interpretation, status=status)


def _loads_jsonp(raw: str) -> object:
    stripped = raw.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        return json.loads(stripped)
    start = stripped.find("(")
    end = stripped.rfind(")")
    if start >= 0 and end > start:
        return json.loads(stripped[start + 1 : end])
    raise ValueError("unsupported JSONP response")


def _find_list(payload: object, *keys: str) -> list:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return value
        found = _find_list(value, *keys)
        if found:
            return found
    return []


def _parse_cn_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d"):
        try:
            return datetime.strptime(value[:19], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _cn_announcement_to_filing(item: dict) -> SecFiling | None:
    title = _pick_text(item, "announcementTitle", "title", "secName", "公告标题") or ""
    date = (_pick_text(item, "announcementTime", "adjunctUrlTime", "publishTime", "公告日期") or "")[:10]
    url = _pick_text(item, "adjunctUrl", "url", "公告链接") or ""
    if url and not url.startswith("http"):
        url = f"http://static.cninfo.com.cn/{url.lstrip('/')}"
    if not title or not date:
        return None
    return SecFiling(form="公告/年报", filing_date=date, report_date=date, accession_number=str(item.get("announcementId", "")), primary_document=title, description=title, url=url)

def score_news(title: str, keywords: list[str]) -> int:
    """Score higher-quality, market-moving headlines above generic market wrap."""

    lowered = title.lower()
    score = 0
    for keyword in keywords:
        if keyword.lower() in lowered:
            score += 3
    if re.search(r"\b(q[1-4]|earnings|guidance|revenue|eps)\b", lowered):
        score += 4
    if re.search(r"\b(upgrade|downgrade|raises|cuts|beats|misses)\b", lowered):
        score += 2
    return score



def _fetch_quote_summary(symbol: str, app_config: AppConfig, modules: list[str]) -> tuple[dict | None, str | None]:
    url = YAHOO_QUOTE_SUMMARY_URL.format(symbol=quote_plus(symbol), modules=quote_plus(",".join(modules)))
    try:
        return json.loads(_get_text(url, app_config)), None
    except (ValueError, URLError, HTTPError) as exc:
        return None, str(exc)


def _quote_summary_result(payload: dict | None) -> dict:
    if not isinstance(payload, dict):
        return {}
    result = payload.get("quoteSummary", {}).get("result")
    if isinstance(result, list) and result:
        return result[0] if isinstance(result[0], dict) else {}
    return {}


def _display_value(value: object) -> str | None:
    if isinstance(value, dict):
        if value.get("fmt") is not None:
            return str(value["fmt"])
        if value.get("longFmt") is not None:
            return str(value["longFmt"])
        value = value.get("raw")
    if value is None:
        return None
    return str(value)


def _raw_number(value: object) -> float | None:
    if isinstance(value, dict):
        value = value.get("raw")
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _metric(label: str, value: object, interpretation: str, status: str) -> FundamentalMetric:
    return FundamentalMetric(label=label, value=_display_value(value) or "N/A", interpretation=interpretation, status=status)


def _status_from_percent(value: object, positive: float, negative: float) -> str:
    raw = _raw_number(value)
    if raw is None:
        return "neutral"
    if raw >= positive:
        return "positive"
    if raw < negative:
        return "negative"
    return "neutral"


def _status_from_inverse(value: object, positive_ceiling: float, negative_floor: float) -> str:
    raw = _raw_number(value)
    if raw is None:
        return "neutral"
    if raw <= positive_ceiling:
        return "positive"
    if raw >= negative_floor:
        return "negative"
    return "neutral"

def _to_float(value: object) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _last_number(values: list[object]) -> int | None:
    for value in reversed(values):
        if isinstance(value, (int, float)):
            return int(value)
    return None


def _parse_rss_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _strip_html(value: object) -> str | None:
    if value is None:
        return None
    return re.sub(r"<[^>]+>", "", str(value)).strip() or None


def fetch_sec_fundamentals(symbol: str, app_config: AppConfig, lookback_days: int = 365) -> SecFundamentalData:
    """Fetch the last year of SEC EDGAR filings and XBRL facts for a ticker.

    Data is sourced from the same EDGAR corpus exposed by https://www.sec.gov/edgar/search:
    company submissions identify recent 10-K/10-Q filings, while companyfacts
    supplies the standardized financial statement datapoints used for charts.
    """

    normalized_symbol = symbol.strip().upper()
    try:
        cik = _lookup_sec_cik(normalized_symbol, app_config)
        if not cik:
            return SecFundamentalData(symbol=normalized_symbol, source="SEC EDGAR submissions/companyfacts", error="SEC CIK not found for ticker")
        submissions = json.loads(_get_text(SEC_SUBMISSIONS_URL.format(cik=cik), app_config))
        filings = _parse_recent_sec_filings(cik, submissions, lookback_days)
        facts_payload = json.loads(_get_text(SEC_COMPANY_FACTS_URL.format(cik=cik), app_config))
        facts = _parse_recent_sec_facts(facts_payload, lookback_days)
        if not filings and not facts:
            return SecFundamentalData(symbol=normalized_symbol, cik=cik, source="SEC EDGAR submissions/companyfacts", error="no 10-K/10-Q filings or facts found in the last year")
        return SecFundamentalData(symbol=normalized_symbol, cik=cik, filings=filings, facts=facts, source="SEC EDGAR submissions/companyfacts")
    except (ValueError, TypeError, KeyError, URLError, HTTPError) as exc:
        return SecFundamentalData(symbol=normalized_symbol, source="SEC EDGAR submissions/companyfacts", error=str(exc))


def _lookup_sec_cik(symbol: str, app_config: AppConfig) -> str | None:
    payload = json.loads(_get_text(SEC_TICKERS_URL, app_config))
    for company in payload.values() if isinstance(payload, dict) else []:
        if isinstance(company, dict) and str(company.get("ticker", "")).upper() == symbol:
            return str(company.get("cik_str", "")).zfill(10)
    return None


def _parse_recent_sec_filings(cik: str, submissions: dict, lookback_days: int) -> list[SecFiling]:
    recent = submissions.get("filings", {}).get("recent", {}) if isinstance(submissions, dict) else {}
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=lookback_days)
    filings: list[SecFiling] = []
    for index, form in enumerate(recent.get("form", [])):
        if form not in {"10-K", "10-Q"}:
            continue
        filing_date = _recent_value(recent, "filingDate", index)
        report_date = _recent_value(recent, "reportDate", index)
        if not _is_recent_iso_date(filing_date or report_date, cutoff):
            continue
        accession = _recent_value(recent, "accessionNumber", index)
        document = _recent_value(recent, "primaryDocument", index)
        if not accession or not document:
            continue
        accession_path = accession.replace("-", "")
        cik_path = str(int(cik))
        url = SEC_ARCHIVES_DOC_URL.format(cik=cik_path, accession=accession_path, document=document)
        filings.append(
            SecFiling(
                form=form,
                filing_date=filing_date or "",
                report_date=report_date or "",
                accession_number=accession,
                primary_document=document,
                description=_recent_value(recent, "primaryDocDescription", index) or "",
                url=url,
            )
        )
    return sorted(filings, key=lambda filing: filing.filing_date, reverse=True)


def _parse_recent_sec_facts(payload: dict, lookback_days: int) -> dict[str, list[SecFactPoint]]:
    us_gaap = payload.get("facts", {}).get("us-gaap", {}) if isinstance(payload, dict) else {}
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=lookback_days)
    facts: dict[str, list[SecFactPoint]] = {}
    for label, tags in SEC_FACT_TAGS.items():
        points: list[SecFactPoint] = []
        for tag in tags:
            tag_payload = us_gaap.get(tag)
            if not isinstance(tag_payload, dict):
                continue
            for unit, values in tag_payload.get("units", {}).items():
                if unit not in {"USD", "USD/shares"}:
                    continue
                for item in values:
                    if item.get("form") not in {"10-K", "10-Q"}:
                        continue
                    end_date = str(item.get("end", ""))
                    filed_date = str(item.get("filed", ""))
                    if not _is_recent_iso_date(end_date or filed_date, cutoff):
                        continue
                    value = _to_float(item.get("val"))
                    if value is None:
                        continue
                    points.append(
                        SecFactPoint(
                            label=label,
                            tag=tag,
                            fiscal_period=str(item.get("fp", "")),
                            fiscal_year=_int_or_none(item.get("fy")),
                            end_date=end_date,
                            filed_date=filed_date,
                            form=str(item.get("form", "")),
                            value=value,
                            unit=unit,
                        )
                    )
            if points:
                break
        if points:
            facts[label] = _dedupe_fact_points(points)[:6]
    return facts


def _dedupe_fact_points(points: list[SecFactPoint]) -> list[SecFactPoint]:
    unique: dict[tuple[str, str, str], SecFactPoint] = {}
    for point in sorted(points, key=lambda item: (item.end_date, item.filed_date), reverse=True):
        key = (point.end_date, point.fiscal_period, point.form)
        unique.setdefault(key, point)
    return sorted(unique.values(), key=lambda item: item.end_date, reverse=True)


def _recent_value(recent: dict, key: str, index: int) -> str:
    values = recent.get(key, [])
    if isinstance(values, list) and index < len(values):
        return str(values[index] or "")
    return ""


def _is_recent_iso_date(value: str, cutoff) -> bool:
    try:
        return datetime.fromisoformat(value).date() >= cutoff
    except ValueError:
        return False


def _int_or_none(value: object) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None
