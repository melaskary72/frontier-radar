"""Command line entry point: `radar collect <source> --since YYYY-MM-DD [--sector s]`."""

from __future__ import annotations

import argparse
import logging
from datetime import date, timedelta

from radar.config import thesis
from radar.store.db import create_all, session_scope

log = logging.getLogger("radar")

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
    if args.source == "edgar":
        with session_scope() as s:
            d, n, col = since, 0, EdgarCollector(s)
            while d <= until:
                n += len(col.discover_day(d))
                d += timedelta(days=1)
        print(f"edgar: {n} in-thesis Form D filings {since} to {until}")
        return
    cls = {"github": GitHubCollector, "arxiv": ArxivCollector, "hn": HNCollector,
           "sbir": SBIRCollector, "usaspending": USAspendingCollector}[args.source]
    failed = []
    for sector in sectors:
        # One transaction per sector: a rate-limit failure in one sector keeps the others.
        try:
            with session_scope() as s:
                col = cls(s)
                rows = col.discover(sector, since, until)
                print(f"{args.source}/{sector}: {len(rows)} rows ({col.requests_made} requests)")
        except Exception as exc:  # report and continue with the next sector
            log.error("%s/%s failed: %s", args.source, sector, exc)
            failed.append(sector)
    if failed:
        raise SystemExit(f"{args.source}: failed sectors {failed}")


def cmd_score(args: argparse.Namespace) -> None:
    from radar.pipeline import RunConfig, describe, run

    create_all()
    with session_scope() as s:
        report = run(s, RunConfig(as_of=args.as_of, pit_only=args.pit_only, enrich_top=args.enrich_top,
                                  github_budget=args.github_budget, agents=not args.no_agents))
        print(describe(s, report))


def cmd_memo(args: argparse.Namespace) -> None:
    from radar.agents.research import write_memo
    from radar.store.models import Company

    with session_scope() as s:
        company = s.get(Company, args.company_id)
        if company is None:
            raise SystemExit(f"no company {args.company_id}")
        pub = write_memo(s, company, args.as_of)
        print(pub.memo.body_md)
        print(f"verifier: {pub.summary}")


def cmd_refit(args: argparse.Namespace) -> None:
    from radar.scoring.feedback import MIN_LABELS, propose_weights, write_history

    with session_scope() as s:
        out = propose_weights(s)
    if out is None:
        print(f"Not enough feedback to refit (need {MIN_LABELS}+ companies with both thumbs up and down).")
        return
    print(write_history(*out))


def cmd_review(args: argparse.Namespace) -> None:
    from datetime import UTC, datetime

    from sqlalchemy import select

    from radar.store.models import EntityCandidate

    with session_scope() as s:
        if args.decide:
            cid, verdict = args.decide
            c = s.get(EntityCandidate, int(cid))
            if c is None:
                raise SystemExit(f"no candidate {cid}")
            c.status = {"y": "approved", "n": "rejected"}[verdict]
            c.decided_by, c.decided_at = "human", datetime.now(UTC)
            print(f"{cid}: {c.status}")
            return
        for c in s.scalars(select(EntityCandidate).where(EntityCandidate.status == "pending")):
            print(f"{c.id:>5}  {c.score:5.1f}  {c.a_label[:50]:<50}  <>  {c.b_label[:50]:<50}  {c.rationale}")


def cmd_export(args: argparse.Namespace) -> None:
    """Write the dashboard JSON (web/public/data/) from a scored run database."""
    from pathlib import Path

    from radar.config import REPO_ROOT
    from radar.export import OUT_DIR, export
    from radar.store.db import make_engine

    def url(path: str) -> str:
        return path if "://" in path else f"sqlite:///{Path(path).resolve()}"

    db = args.db or str(REPO_ROOT / "data" / "live.db")
    extras = [p for p in (args.agent_db or [str(REPO_ROOT / "data" / "radar.db")]) if "://" in p or Path(p).exists()]
    with session_scope(make_engine(url(db))) as s:
        others = [(Path(p).stem, session_scope(make_engine(url(p)))) for p in extras]
        sessions = [(label, cm.__enter__()) for label, cm in others]
        try:
            counts = export(s, Path(args.out) if args.out else OUT_DIR, extra_agent_sessions=sessions)
        finally:
            for _label, cm in others:
                cm.__exit__(None, None, None)
    log.info("exported %s", counts)


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
    sc = sub.add_parser("score", help="resolve, enrich, and score as of a date")
    sc.add_argument("--as-of", type=_date, default=date.today())
    sc.add_argument("--pit-only", action="store_true", help="backtest mode: point-in-time features only")
    sc.add_argument("--enrich-top", type=int, default=150)
    sc.add_argument("--github-budget", type=int, default=3000)
    sc.add_argument("--no-agents", action="store_true", help="skip LLM judges, why-now lines, and memos")
    sc.set_defaults(func=cmd_score)
    mm = sub.add_parser("memo", help="write and verify a memo for one company")
    mm.add_argument("company_id", type=int)
    mm.add_argument("--as-of", type=_date, default=date.today())
    mm.set_defaults(func=cmd_memo)
    rf = sub.add_parser("refit", help="propose new group weights from feedback (never applied)")
    rf.set_defaults(func=cmd_refit)
    rv = sub.add_parser("review", help="list pending entity matches, or decide one")
    rv.add_argument("--decide", nargs=2, metavar=("ID", "Y_OR_N"))
    rv.set_defaults(func=cmd_review)
    ex = sub.add_parser("export", help="write dashboard JSON from a scored run database")
    ex.add_argument("--db", help="scored run database (default data/live.db)")
    ex.add_argument("--agent-db", action="append", help="extra database whose agent runs to include (default data/radar.db)")
    ex.add_argument("--out", help="output directory (default web/public/data)")
    ex.set_defaults(func=cmd_export)
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    args.func(args)


if __name__ == "__main__":
    main()
