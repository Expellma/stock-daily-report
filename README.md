# Stock Daily Report

一个本地运行的美股每日关注标的行情海报生成工程，面向买方投研场景：每天早上 8 点自动抓取关注列表行情、财报与重大业务新闻，并输出适合自媒体传播的简洁海报。

## 功能概览

- **关注列表日报**：读取 `config/watchlist.csv`，生成每个关注标的的价格表现、成交量、财报日历与高质量新闻摘要。
- **标普 500 重大新闻**：读取 `config/sp500_symbols.csv`，批量扫描标普 500 相关重大新闻，并按关键词与时间排序。
- **海报输出**：使用纯 SVG 模版生成海报，默认输出到 `outputs/YYYY-MM-DD/`。
- **本地定时**：内置 `scheduler` 命令，可按配置每天本地时间 08:00 自动执行；也提供 cron/systemd 示例。
- **配置化**：运行时间、时区、输出目录、新闻关键词、请求超时、海报尺寸等均在 `config/settings.toml` 中配置。

## 快速开始

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
stock-daily-report run --config config/settings.toml
```

生成结果：

- `outputs/<date>/daily_report.json`：结构化数据，便于二次分发。
- `outputs/<date>/daily_poster.svg`：每日行情海报，可直接发布或转成 PNG。

## 每日 8 点定时运行

### 方式一：内置调度器

```bash
stock-daily-report scheduler --config config/settings.toml
```

`config/settings.toml` 中默认：

```toml
[schedule]
time = "08:00"
timezone = "America/New_York"
```

### 方式二：cron

```cron
0 8 * * 1-5 cd /path/to/stock-daily-report && /path/to/python -m stock_daily_report.cli run --config config/settings.toml
```

## 配置说明

- `config/watchlist.csv`：买方重点关注标的。
- `config/sp500_symbols.csv`：标普 500 扫描池，可按需要维护完整列表。
- `config/settings.toml`：数据源、关键词、调度时间、海报样式。

> 默认数据源使用 Yahoo Finance Chart/RSS 与 Nasdaq 财报 API，均无需 API Key；网络不可用时会生成降级海报并在 JSON 中记录错误，保证任务可观测。

## 项目结构

```text
stock_daily_report/
  cli.py          # CLI 入口：run / scheduler
  config.py       # 配置加载与默认值
  data_sources.py # 行情、新闻、财报数据采集
  report.py       # 报告编排与排序逻辑
  poster.py       # SVG 海报绘制
  scheduler.py    # 本地每日调度循环
```
