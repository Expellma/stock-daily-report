"""Command-line interface for local report generation."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from .config import load_settings
from .fisher import build_fisher_analysis, output_fisher_dir_for, write_fisher_markdown
from .scheduler import run_forever, run_once


def main() -> None:
    config_parent = argparse.ArgumentParser(add_help=False)
    config_parent.add_argument("--config", default="config/settings.toml", help="Path to TOML configuration file.")
    parser = argparse.ArgumentParser(description="Generate US stock reports and Fisher fundamental analysis.", parents=[config_parent])
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("run", parents=[config_parent], help="Generate the daily watchlist JSON and SVG poster once.")
    subparsers.add_parser("scheduler", parents=[config_parent], help="Start the local daily scheduler.")

    fisher_parser = subparsers.add_parser("fisher", parents=[config_parent], help="Generate a Fisher growth-investing Markdown analysis for one symbol.")
    fisher_parser.add_argument("symbol", help="Ticker symbol, for example NVDA or MSFT.")
    fisher_parser.add_argument("--name", help="Optional company display name override.")
    fisher_parser.add_argument("--thesis", default="", help="Optional custom investment thesis or GPT-style setup note.")
    fisher_parser.add_argument("--output-dir", type=Path, help="Optional output directory; defaults to outputs/<date>/fisher/.")
    fisher_parser.add_argument("--annual-report-dir", type=Path, help="Local annual-report directory; defaults to /input/<name-or-symbol>.")

    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = load_settings(args.config)
    if args.command == "run":
        json_path, poster_path = run_once(settings)
        print(f"report={json_path}")
        print(f"poster={poster_path}")
    elif args.command == "fisher":
        annual_report_dir = args.annual_report_dir or Path("/input") / (args.name or args.symbol)
        analysis = build_fisher_analysis(settings, args.symbol, name=args.name, thesis=args.thesis, annual_report_dir=annual_report_dir)
        output_dir = args.output_dir or output_fisher_dir_for(settings, analysis.generated_at)
        markdown_path = write_fisher_markdown(analysis, output_dir)
        print(f"fisher_markdown={markdown_path}")
    else:
        run_forever(settings)


if __name__ == "__main__":
    main()
