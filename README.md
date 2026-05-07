# Stock Daily Report

一个本地运行的美股每日关注标的行情海报生成工程，面向买方投研场景：每天早上 8 点自动抓取关注列表行情、财报与重大业务新闻，并输出适合自媒体传播的简洁海报。

## 功能概览

- **关注列表日报**：读取 `config/watchlist.csv`，生成每个关注标的的价格表现、成交量、财报日历与高质量新闻摘要。
- **标普 500 重大新闻**：读取 `config/sp500_symbols.csv`，批量扫描标普 500 相关重大新闻，并按关键词与时间排序。
- **海报输出**：使用纯 SVG 模版生成海报，默认输出到 `outputs/YYYY-MM-DD/`。
- **本地定时**：内置 `scheduler` 命令，可按配置每天本地时间 08:00 自动执行；也提供 cron/systemd 示例。
- **配置化**：运行时间、时区、输出目录、新闻关键词、请求超时、海报尺寸等均在 `config/settings.toml` 中配置。
- **费雪成长投资分析**：对指定标的抓取公司画像、行情、SEC EDGAR 近一年 10-K/10-Q 财报、关键 XBRL 基本面、财报日期和高信号新闻，按 Philip Fisher 15 问生成适合浏览与二次编辑的 Markdown。

## 快速开始

### macOS / Linux

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
stock-daily-report run --config config/settings.toml
```

### Windows（Git Bash / MSYS2 / Cygwin / WSL）

Windows 下直接执行 `source .venv/bin/activate` 经常会因为虚拟环境目录为 `.venv/Scripts/` 而失败。推荐使用仓库内置脚本自动创建/复用虚拟环境、选择正确的 Python 路径，并直接以模块方式运行命令：

```bash
sh scripts/windows_run.sh run
```

常用示例：

```bash
sh scripts/windows_run.sh fisher NVDA --thesis "AI accelerator demand"
sh scripts/windows_run.sh scheduler
```

如需额外生成 `stock-daily-report` 控制台命令，可显式安装项目；脚本会使用 `--no-build-isolation`，减少 Windows/代理环境下联网拉取构建依赖导致的失败：

```bash
INSTALL_PROJECT=1 sh scripts/windows_run.sh run
```

生成结果：

- `outputs/<date>/daily_report.json`：结构化数据，便于二次分发。
- `outputs/<date>/daily_poster.svg`：每日行情海报，可直接发布或转成 PNG。

## 费雪成长投资 Markdown 分析

对任意指定标的生成基本面初筛报告：

```bash
stock-daily-report fisher NVDA --config config/settings.toml --thesis "AI accelerator demand and data-center capex bellwether"
stock-daily-report fisher 600519.SH --name 贵州茅台 --annual-report-dir /input/贵州茅台
```

生成结果：

- `outputs/<date>/fisher/nvda_fisher_analysis.md`：包含一页结论、公司画像、关键基本面仪表盘、SEC EDGAR 近一年 10-K/10-Q 财报表格、本地年报文件分析、带图标与迷你趋势图的关键 XBRL 数据、费雪 15 问逐项评分、近期高信号新闻和下一步尽调清单。

本地年报目录可通过 `--annual-report-dir` 指定；未传入时默认读取 `/input/<标的名>`，其中 `<标的名>` 优先使用 `--name`，否则使用 `symbol`。当前会直接解析 `.txt` / `.md` 年报片段，`.pdf` 会在报告中标记为暂不支持并继续生成报告。

该报告定位为“费雪框架初筛 + SEC 财报数据面板 + 本地年报证据 + 尽调问题清单”，会在公开数据不足时显式标记待验证项，方便继续补充年报、电话会纪要、专家访谈或你在 GPT 中沉淀的个性化投资主线。SEC 数据来自 EDGAR submissions/companyfacts（与 https://www.sec.gov/edgar/search 同源），默认筛选最近 365 天内提交或报告期结束的 10-K/10-Q。

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
  cli.py          # CLI 入口：run / scheduler / fisher
  config.py       # 配置加载与默认值
  data_sources.py # 行情、新闻、财报与基本面数据采集
  fisher.py       # 费雪成长投资分析与 Markdown 渲染
  report.py       # 报告编排与排序逻辑
  poster.py       # SVG 海报绘制
  scheduler.py    # 本地每日调度循环
```
