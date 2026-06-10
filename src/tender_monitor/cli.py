from __future__ import annotations

import argparse
import logging

from tender_monitor.config import load_settings
from tender_monitor.runner import run_monitor


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Monitoring veřejných zakázek")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("run", help="Spustí monitoring a vygeneruje reporty")
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = build_parser().parse_args(argv)
    if args.command == "run":
        settings = load_settings()
        excel_path, html_path, count = run_monitor(settings)
        print(f"Hotovo. Zakázky v reportu: {count}")
        print(f"Excel: {excel_path}")
        print(f"HTML: {html_path}")
        return 0
    return 1
