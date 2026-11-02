"""Command line entry point: `radar collect <source> --since YYYY-MM-DD [--sector s]`."""

from __future__ import annotations

import argparse
import logging
from datetime import date, timedelta

from radar.config import thesis
from radar.store.db import create_all, session_scope

SOURCES = ["github", "arxiv", "hn", "sbir", "usaspending", "edgar"]


def _date(s: str) -> date:
    return date.fromisoformat(s)


def cmd_collect(args: argparse.Namespace) -> None:
    from radar.collectors.arxiv import ArxivCollector
    from radar.collectors.edgar import EdgarCollector
    from radar.collectors.github import GitHubCollector
    from radar.collectors.hn import HNCollector
    from radar.collectors.sbir import SBIRCollector
    from radar.collectors.usaspending import USAspendingCollector

    since: date = args.since
    until: date = args.until or date.today()
    sectors = [args.sector] if args.sector else list(thesis().sectors)
    create_all()
    with session_scope() as s:
        if args.source == "edgar":
            d, n = since, 0
            col = EdgarCollector(s)
            while d <= until:
                n += len(col.discover_day(d))
                d += timedelta(days=1)
            print(f"edgar: {n} in-thesis Form D filings {since} to {until}")
            return
        cls = {"github": GitHubCollector, "arxiv": ArxivCollector, "hn": HNCollector,
               "sbir": SBIRCollector, "usaspending": USAspendingCollector}[args.source]
        col = cls(s)
        for sector in sectors:
            rows = col.discover(sector, since, until)
            print(f"{args.source}/{sector}: {len(rows)} rows ({col.requests_made} requests so far)")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="radar")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect", help="run one collector over a date window")
    c.add_argument("source", choices=SOURCES)
    c.add_argument("--since", type=_date, required=True)
    c.add_argument("--until", type=_date)
    c.add_argument("--sector", choices=list(thesis().sectors))
    c.set_defaults(func=cmd_collect)
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    args.func(args)


if __name__ == "__main__":
    main()
