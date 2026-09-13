from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date

import uvicorn

from ecochronos_vault.config import get_settings
from ecochronos_vault.ingest import build_ingest_status_view, run_daily_ingest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ecochronos-vault",
        description="EcoChronos Vault API and daily OpenAQ ingest.",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("serve", help="Run the HTTP API (default)")

    ingest_parser = sub.add_parser(
        "ingest",
        help="Fetch OpenAQ locations once and write data/raw/openaq/<date>/",
    )
    ingest_parser.add_argument(
        "--date",
        dest="run_date",
        metavar="YYYY-MM-DD",
        type=date.fromisoformat,
        default=None,
        help="UTC calendar day to store under (default: today UTC)",
    )
    ingest_parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Do not overwrite an artifact that already exists for that day",
    )

    sub.add_parser("ingest-status", help="Print last ingest status as JSON")
    return parser


def _serve() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level.upper())
    uvicorn.run(
        "ecochronos_vault.app:app",
        host=settings.api_host,
        port=settings.api_port,
        log_level=settings.log_level.lower(),
    )


def _run_ingest(args: argparse.Namespace) -> int:
    get_settings.cache_clear()
    settings = get_settings()
    logging.basicConfig(level=settings.log_level.upper())
    result = run_daily_ingest(
        settings,
        run_date=args.run_date,
        skip_existing=True if args.skip_existing else None,
    )
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    return 0 if result.status != "error" else 1


def _print_status() -> int:
    get_settings.cache_clear()
    settings = get_settings()
    print(json.dumps(build_ingest_status_view(settings), indent=2, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command in (None, "serve"):
        _serve()
        return 0
    if args.command == "ingest":
        return _run_ingest(args)
    if args.command == "ingest-status":
        return _print_status()
    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
