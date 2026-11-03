"""Portfolio Stats: seed the portfolio and record public proxies (brief Part 5.10).

Public data only. These are proxies, not Anti Fund's actual marks or metrics.

Every fact is fetched through the radar's cached, rate-limited collectors, so it has a raw
snapshot (fetch time and SHA-256); the snapshot id is stored inside the fact's value. Press
rounds are hand-curated in press_rounds.csv with public sources. Reruns on the same day
replace that day's facts (upsert by company, fact_type, as_of).

Fact values are JSON strings with at least {"n": number or null, "text": human-readable}, plus
"snapshot_id" for fetched facts. Fact types:
    open_roles         open postings on the company's verified ATS board
    github_stars       stars summed over the org's 100 most recently pushed public repositories
    github_pushed_90d  of those, repositories pushed in the last 90 days
    tranco_rank        latest Tranco list rank for the company domain
    form_d_filing      most recent SEC Form D whose filer name matches the company exactly
                       (after dropping legal and descriptive suffixes such as "Industries, Inc.")
    spv_form_d_24m     Form D filings in the last 24 months by investment vehicles (SPVs, fund
                       series) whose names include the company name: a public proxy for
                       secondary-market demand, not a filing by the company itself
    press_round        a publicly reported round (press_rounds.csv)

Run: DATABASE_URL=sqlite:///data/live.db uv run python portfolio/collect.py
"""

from __future__ import annotations

import csv
import json
import logging
import os
import re
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("DATABASE_URL", f"sqlite:///{ROOT / 'data' / 'live.db'}")
sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from radar.collectors.ats import ATSCollector  # noqa: E402
from radar.collectors.common import clean_text, parse_dt  # noqa: E402
from radar.collectors.edgar import EdgarCollector  # noqa: E402
from radar.collectors.github import GitHubCollector  # noqa: E402
from radar.collectors.tranco import TrancoCollector  # noqa: E402
from radar.entities.resolve import loose_name, strict_name  # noqa: E402
from radar.store.db import create_all, session_scope  # noqa: E402
from radar.store.models import PortfolioCompany, PortfolioFact  # noqa: E402

HERE = Path(__file__).resolve().parent
FORM_D_LOOKBACK_DAYS = 730
# Names that are ordinary words or shared by many filers: an exact-name Form D match would not
# identify the portfolio company, so Form D is not searched for them.
AMBIGUOUS_FOR_FORM_D = {"merge", "trajectory", "aeon", "enigma", "orbital", "archive", "lighter",
                        "natural", "liquid", "monaco", "catalyst", "modal", "ramp", "cognition",
                        "helion", "etched", "entropy", "melius", "pensive", "erebor"}
VEHICLE = re.compile(r"\b(spv|series|fund|ventures|capital|holdings|partners|opportunit)", re.I)
BOARD_URL = {"greenhouse": "https://boards.greenhouse.io/{}", "lever": "https://jobs.lever.co/{}",
             "ashby": "https://jobs.ashbyhq.com/{}"}
log = logging.getLogger("portfolio")


def load_companies() -> list[dict[str, str]]:
    with open(HERE / "companies.csv", newline="") as f:
        return list(csv.DictReader(f))


def put_fact(session: Session, company_id: int, fact_type: str, value: dict[str, Any], source_url: str,
             as_of: date) -> PortfolioFact:
    value = {k: (clean_text(v) if isinstance(v, str) else v) for k, v in value.items()}
    row = session.scalars(select(PortfolioFact).filter_by(company_id=company_id, fact_type=fact_type,
                                                          as_of=as_of)).first()
    if row is None:
        row = PortfolioFact(company_id=company_id, fact_type=fact_type, as_of=as_of, value="", source_url="")
        session.add(row)
    row.value, row.source_url = json.dumps(value), source_url
    session.flush()
    return row


def seed(session: Session, rows: list[dict[str, str]]) -> dict[str, PortfolioCompany]:
    out = {}
    for r in rows:
        c = session.scalars(select(PortfolioCompany).filter_by(name=r["name"])).first()
        if c is None:
            c = PortfolioCompany(name=r["name"], source_url=r["source_url"])
            session.add(c)
        c.sector = r["category"]
        c.first_invested_year = int(r["first_invested_year"]) if r["first_invested_year"] else None
        c.stage_at_entry = clean_text(r["stage_at_entry"])
        c.source_url = r["source_url"]
        out[r["name"]] = c
    session.flush()
    return out


def open_roles(session: Session, ats: ATSCollector, c: PortfolioCompany, r: dict[str, str], as_of: date) -> int:
    if not (r["ats_provider"] and r["ats_slug"]):
        return 0
    jobs, snap = ats.fetch_board(r["ats_provider"], r["ats_slug"])
    if jobs is None:
        log.warning("%s: ATS board %s/%s did not return", c.name, r["ats_provider"], r["ats_slug"])
        return 0
    eng = sum(1 for j in jobs if "engineer" in (f"{j.get('department') or ''} {j['title']}").lower())
    put_fact(session, c.id, "open_roles",
             {"n": len(jobs), "engineering": eng, "provider": r["ats_provider"], "snapshot_id": snap,
              "text": f"{len(jobs)} open roles ({eng} engineering) on the public {r['ats_provider'].title()} board"},
             BOARD_URL[r["ats_provider"]].format(r["ats_slug"]), as_of)
    return 1


def github(session: Session, gh: GitHubCollector, c: PortfolioCompany, r: dict[str, str], as_of: date) -> int:
    org = r["github_org"]
    if not org:
        return 0
    f = gh.fetch(f"{gh.api}/orgs/{org}/repos", params={"per_page": 100, "sort": "pushed", "type": "public"},
                 ok_statuses=(200, 404))
    repos = f.payload if f.status == 200 and isinstance(f.payload, list) else []
    if not repos:
        return 0
    since = datetime.combine(as_of, datetime.min.time(), UTC) - timedelta(days=90)
    stars = sum(int(x.get("stargazers_count") or 0) for x in repos)
    pushed = sum(1 for x in repos if (parse_dt(x.get("pushed_at")) or since) > since)
    url = f"https://github.com/{org}"
    put_fact(session, c.id, "github_stars",
             {"n": stars, "repos_counted": len(repos), "snapshot_id": f.snapshot_id,
              "text": f"{stars:,} stars across {len(repos)} public repositories (most recently pushed first)"},
             url, as_of)
    put_fact(session, c.id, "github_pushed_90d",
             {"n": pushed, "repos_counted": len(repos), "snapshot_id": f.snapshot_id,
              "text": f"{pushed} of {len(repos)} public repositories pushed in the last 90 days"}, url, as_of)
    return 2


def tranco(session: Session, tr: TrancoCollector, c: PortfolioCompany, r: dict[str, str], as_of: date) -> int:
    if not r["domain"]:
        return 0
    rows = tr.ranks(r["domain"])
    rank = TrancoCollector.rank_as_of(rows, as_of)
    if rank is None:
        return 0
    latest = max((x for x in rows if x.list_date <= as_of), key=lambda x: x.list_date)
    put_fact(session, c.id, "tranco_rank",
             {"n": rank, "list_date": latest.list_date.isoformat(), "snapshot_id": latest.snapshot_id,
              "history": [[x.list_date.isoformat(), x.rank] for x in sorted(rows, key=lambda x: x.list_date)][-30:],
              "text": f"Tranco rank {rank:,} on the {latest.list_date.isoformat()} list"},
             f"https://tranco-list.eu/query?domain={r['domain']}", as_of)
    return 1


def form_d(session: Session, ed: EdgarCollector, c: PortfolioCompany, r: dict[str, str], as_of: date) -> int:
    key = strict_name(c.name)
    if key in AMBIGUOUS_FOR_FORM_D:
        return 0
    hits = ed.search_form_d(c.name, as_of - timedelta(days=FORM_D_LOOKBACK_DAYS), as_of)
    target = loose_name(c.name)
    exact = [h for h in hits if loose_name(h["company_name"]) == target and h.get("filed_at")]
    word = re.compile(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])")
    vehicles = {h["accession"]: h for h in hits if h not in exact and word.search(strict_name(h["company_name"]))
                and VEHICLE.search(h["company_name"])}
    n = 0
    if vehicles:
        q = f'https://efts.sec.gov/LATEST/search-index?q="{c.name}"&forms=D'
        put_fact(session, c.id, "spv_form_d_24m",
                 {"n": len(vehicles), "snapshot_id": next(iter(vehicles.values()))["snapshot_id"],
                  "examples": sorted({h["company_name"] for h in vehicles.values()})[:5],
                  "text": f"{len(vehicles)} Form D filing{'s' if len(vehicles) != 1 else ''} in 24 months by "
                          "investment vehicles named for the company"},
                 q, as_of)
        n += 1
    if not exact:
        return n
    exact.sort(key=lambda h: h["filed_at"], reverse=True)
    h = exact[0]
    acc = h["accession"].replace("-", "")
    url = f"https://www.sec.gov/Archives/edgar/data/{h['cik']}/{acc}/"
    put_fact(session, c.id, "form_d_filing",
             {"n": len(exact), "filed_at": h["filed_at"], "filer": h["company_name"], "cik": h["cik"],
              "snapshot_id": h["snapshot_id"],
              "text": f"Form D filed {h['filed_at']} by {h['company_name']} ({len(exact)} in the last two years)"},
             url, as_of)
    return n + 1


def press(session: Session, companies: dict[str, PortfolioCompany], as_of: date) -> int:
    n = 0
    with open(HERE / "press_rounds.csv", newline="") as f:
        for r in csv.DictReader(f):
            c = companies.get(r["company"])
            if c is None:
                continue
            amt = int(r["amount_usd"]) if r["amount_usd"] else None
            money = f"${amt / 1e6:,.1f}M " if amt else ""
            put_fact(session, c.id, "press_round",
                     {"n": amt, "round": r["round"], "announced_on": r["announced_on"], "note": r["note"],
                      "curated": True, "text": f"{money}{r['round']} reported {r['announced_on']}"},
                     r["source_url"], as_of)
            n += 1
    return n


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    create_all()
    as_of = date.today()
    rows = load_companies()
    counts: dict[str, int] = {}
    with session_scope() as s:
        companies = seed(s, rows)
        ats, gh, tr, ed = ATSCollector(s), GitHubCollector(s), TrancoCollector(s), EdgarCollector(s)
        for r in rows:
            c = companies[r["name"]]
            for label, fn, coll in (("open_roles", open_roles, ats), ("github", github, gh),
                                    ("tranco", tranco, tr), ("form_d", form_d, ed)):
                try:
                    counts[label] = counts.get(label, 0) + fn(s, coll, c, r, as_of)  # type: ignore[arg-type]
                except Exception as exc:  # one source failing must not stop the rest
                    log.warning("%s: %s failed (%s: %s)", c.name, label, type(exc).__name__, exc)
            s.commit()
        counts["press_round"] = press(s, companies, as_of)
    log.info("seeded %d companies; facts written %s", len(rows), counts)


if __name__ == "__main__":
    main()
