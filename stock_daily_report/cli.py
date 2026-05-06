"""Command-line interface for local report generation."""

from __future__ import annotations

import argparse
import logging

from .config import load_settings
from .scheduler import run_forever, run_once


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate daily US stock report posters.")
    parser.add_argument("command", choices=["run", "scheduler"], help="Run once or start the local daily scheduler.")
    parser.add_argument("--config", default="config/settings.toml", help="Path to TOML configuration file.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = load_settings(args.config)
    if args.command == "run":
        json_path, poster_path = run_once(settings)
        print(f"report={json_path}")
        print(f"poster={poster_path}")
    else:
        run_forever(settings)


if __name__ == "__main__":
    main()
