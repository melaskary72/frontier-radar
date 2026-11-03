"""Point-in-time backtest on Anti Fund's public early-stage portfolio.

For every company in rounds.csv and every offset (T minus 90 and T minus 30 days, where T is
the public announcement date):

1. Run the full radar pipeline as of the cutoff in backtest mode: point-in-time features
   only, no agents, universe limited to entities first seen in the 12 months before the
   cutoff, candidates drawn only from rows that thesis queries surfaced.
2. Look for the target in the results (here, in eval/, where names are allowed): by domain,
   by a GitHub account whose homepage field is the company domain (footprints.csv), or by an
   exact distinctive name.
3. Record coverage at each stage (ever collected, in universe, passed thesis gate,
   enriched, scored), rank, and the stars baseline rank in the same universe.

Also runs the query audit: no discovery query may contain a target name or domain, and
every company in a universe must trace to at least one discovery row.

Run after eval/backtest/collect.py: uv run python eval/backtest/run_backtest.py
Writes eval/backtest/results.json.
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import os
import re
import statistics
import sys
import time
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("DATABASE_URL", f"sqlite:///{ROOT / 'data' / 'backtest.db'}")
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

from radar.entities.resolve import distinctive, strict_name  # noqa: E402
from radar.pipeline import RunConfig, run  # noqa: E402
from radar.store.db import session_scope  # noqa: E402
from radar.store.models import Company, Discovery, Edge, Repo  # noqa: E402

HERE = Path(__file__).resolve().parent
CUTOFF_CACHE = ROOT / "data" / "backtest_cutoffs"
OFFSETS = (90, 30)
ENRICH_TOP = 60
SCORE_TOP = 600
LOOKBACK_DAYS = 365
log = logging.getLogger("backtest")


def load_targets() -> list[dict[str, Any]]:
    with open(HERE / "rounds.csv", newline="") as f:
        rows = list(csv.DictReader(f))
    logins: dict[str, list[str]] = defaultdict(list)
    fp = HERE / "footprints.csv"
    if fp.exists():
        with open(fp, newline="") as f:
            for r in csv.DictReader(f):
                for a in json.loads(r["github_accounts"]):
                    if a.get("blog_matches_domain"):
                        logins[r["company"]].append(a["login"].lower())
    for r in rows:
        r["github_logins"] = logins.get(r["company"], [])
        r["announced_on"] = date.fromisoformat(r["announced_on"])
    return rows


def match(target: dict[str, Any], companies: list[Company]) -> list[int]:
    dom = target["domain"].lower()
    out = []
    for c in companies:
        d = (c.domain or "").lower()
        if (d and (d == dom or d.endswith("." + dom))) or (c.github_org and c.github_org.lower() in target["github_logins"]) or (distinctive(target["company"]) and strict_name(c.name) == strict_name(target["company"])):
            out.append(c.id)
    return out


def query_audit(session: Any, targets: list[dict[str, Any]]) -> dict[str, Any]:
    terms = yaml.safe_load((ROOT / "eval" / "leakage_terms.yaml").read_text())["companies"]
    pats = []
    for t in terms:
        if t["mode"] in ("word", "phrase"):
            pats.append((t["name"], re.compile(rf"(?<![\w-]){re.escape(t['name'])}(?![\w-])", re.I)))
        for d in t.get("domains") or []:
            pats.append((t["name"], re.compile(re.escape(d), re.I)))
    queries = list(session.scalars(select(Discovery.query).distinct()))
    hits = sorted({f"{name}: {q}" for q in queries for name, p in pats if p.search(q)})
    companies_without_discovery = 0
    tables = {"repos", "hn_posts", "awards", "papers"}
    for cid in session.scalars(select(Company.id)):
        rows = list(session.execute(select(Edge.dst_type, Edge.dst_id).where(
            Edge.src_type == "company", Edge.src_id == cid, Edge.dst_type.in_(tables))))
        if not rows:
            continue
        found = any(session.scalar(select(func.count()).select_from(Discovery).where(
            Discovery.table_name == t, Discovery.row_id == i)) for t, i in rows)
        companies_without_discovery += 0 if found else 1
    return {"distinct_queries": len(queries), "queries_naming_a_target": hits,
            "companies_without_discovery_row": companies_without_discovery,
            "passed": not hits and companies_without_discovery == 0}


def stars_rank(session: Any, ids: list[int]) -> dict[int, int]:
    """Baseline: total stars of the company's repositories as fetched (today's counts, so the
    baseline sees the future; see BUILD_CONTRACT D5)."""
    stars: dict[int, int] = {}
    for cid in ids:
        repo_ids = list(session.scalars(select(Edge.dst_id).where(Edge.src_type == "company", Edge.src_id == cid,
                                                                  Edge.dst_type == "repos")))
        stars[cid] = int(session.scalar(select(func.coalesce(func.sum(Repo.stars), 0)).where(Repo.id.in_(repo_ids))) or 0) if repo_ids else 0
    order = sorted(ids, key=lambda i: (-stars[i], i))
    return {cid: r for r, cid in enumerate(order, 1)}


def code_fingerprint() -> str:
    """Hash of every file that can change a cutoff's result. Saved cutoffs are reused only
    when it matches, so one results.json never mixes code versions."""
    h = hashlib.sha256()
    files = sorted((ROOT / "radar").rglob("*.py")) + sorted((ROOT / "config").glob("*.yaml"))
    files += [*sorted((ROOT / "radar").rglob("*.md")), ROOT / "eval" / "labels" / "thesis_labels.csv", Path(__file__)]
    for f in files:
        h.update(str(f.relative_to(ROOT)).encode())
        h.update(f.read_bytes())
    return h.hexdigest()[:16]


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    targets = load_targets()
    cutoffs = sorted({t["announced_on"] - timedelta(days=o) for t in targets for o in OFFSETS})
    per_cutoff: dict[str, dict[str, Any]] = {}
    t_start = time.monotonic()
    fingerprint = code_fingerprint()
    CUTOFF_CACHE.mkdir(parents=True, exist_ok=True)
    log.info("code fingerprint %s; %d cutoffs", fingerprint, len(cutoffs))

    for cutoff in cutoffs:
        saved = CUTOFF_CACHE / f"{cutoff.isoformat()}.json"
        if saved.exists():
            prev = json.loads(saved.read_text())
            if prev.get("fingerprint") == fingerprint:
                per_cutoff[cutoff.isoformat()] = prev["result"]
                log.info("cutoff %s: reused saved result (same code)", cutoff)
                continue
        t0 = time.monotonic()
        with session_scope() as s:
            rep = run(s, RunConfig(as_of=cutoff, pit_only=True, agents=False, enrich_top=ENRICH_TOP,
                                   score_top=SCORE_TOP, github_budget=10**7, other_budget=2000,
                                   lookback_days=LOOKBACK_DAYS, wait_on_budget=True))
            baseline = stars_rank(s, rep.in_thesis_ids)
            per_cutoff[cutoff.isoformat()] = {
                "universe": rep.universe_ids, "in_thesis": rep.in_thesis_ids, "enriched": rep.enriched_ids,
                "ranks": {str(x.company_id): x.rank for x in rep.scored_all},
                "baseline_ranks": {str(k): v for k, v in baseline.items()},
                "requests": rep.requests, "seconds": round(time.monotonic() - t0, 1),
            }
        saved.write_text(json.dumps({"fingerprint": fingerprint, "result": per_cutoff[cutoff.isoformat()]}))
        log.info("cutoff %s: universe %d, in thesis %d, scored %d (%.0fs)", cutoff, len(rep.universe_ids),
                 len(rep.in_thesis_ids), len(rep.scored_all), time.monotonic() - t0)

    with session_scope() as s:
        all_companies = list(s.scalars(select(Company)))
        audit = query_audit(s, targets)
        rows = []
        for t in targets:
            ids = match(t, all_companies)
            for o in OFFSETS:
                c = (t["announced_on"] - timedelta(days=o)).isoformat()
                pc = per_cutoff[c]
                hit = lambda key, pc=pc, ids=ids: [i for i in ids if i in set(pc[key])]  # noqa: E731
                ranks = [pc["ranks"][str(i)] for i in ids if str(i) in pc["ranks"]]
                base = [pc["baseline_ranks"][str(i)] for i in ids if str(i) in pc["baseline_ranks"]]
                rows.append({
                    "company": t["company"], "set": t["set"], "included": t["included"] == "yes",
                    "date_confidence": t["date_confidence"], "announced_on": t["announced_on"].isoformat(),
                    "offset_days": o, "cutoff": c, "matched_company_ids": ids,
                    "ever_collected": bool(ids), "in_universe": bool(hit("universe")),
                    "passed_thesis": bool(hit("in_thesis")), "enriched": bool(hit("enriched")),
                    "rank": min(ranks) if ranks else None, "baseline_rank": min(base) if base else None,
                    "universe_size": len(pc["universe"]), "in_thesis_size": len(pc["in_thesis"]),
                })

    def summarize(sel: list[dict[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {"n_companies": len({r["company"] for r in sel})}
        for o in OFFSETS:
            rs = [r for r in sel if r["offset_days"] == o]
            n = len(rs) or 1
            out[f"t_minus_{o}"] = {
                "coverage_in_universe": sum(r["in_universe"] for r in rs),
                "passed_thesis": sum(r["passed_thesis"] for r in rs),
                "recall_at_25": sum(1 for r in rs if r["rank"] and r["rank"] <= 25) / n,
                "recall_at_100": sum(1 for r in rs if r["rank"] and r["rank"] <= 100) / n,
                "baseline_recall_at_100": sum(1 for r in rs if r["baseline_rank"] and r["baseline_rank"] <= 100) / n,
                "random_expected_recall_at_100": round(statistics.mean(
                    [min(1.0, 100 / r["in_thesis_size"]) if r["in_thesis_size"] else 0 for r in rs]) if rs else 0, 4),
                "median_universe": statistics.median([r["universe_size"] for r in rs]) if rs else 0,
                "median_in_thesis": statistics.median([r["in_thesis_size"] for r in rs]) if rs else 0,
            }
        leads = []
        for comp in {r["company"] for r in sel}:
            found = [r["offset_days"] for r in sel if r["company"] == comp and r["rank"] and r["rank"] <= 100]
            if found:
                leads.append(max(found))
        out["median_lead_days_top100"] = statistics.median(leads) if leads else None
        out["companies_found_top100"] = len(leads)
        return out

    results = {
        "generated": date.today().isoformat(),
        "code_fingerprint": fingerprint,
        "protocol": {"offsets_days": list(OFFSETS), "lookback_days": LOOKBACK_DAYS, "enrich_top": ENRICH_TOP,
                     "score_top": SCORE_TOP, "pit_only": True, "agents": False,
                     "baseline": "total GitHub stars as fetched (hindsight-advantaged; BUILD_CONTRACT D5)"},
        "headline": summarize([r for r in rows if r["included"] and r["set"] == "primary"]),
        "secondary": summarize([r for r in rows if r["included"] and r["set"] == "secondary"]),
        "sensitivity_low_confidence_dates": summarize([r for r in rows if not r["included"]]),
        "rows": rows,
        "cutoffs": {c: {k: (len(v) if isinstance(v, list | dict) and k in ("universe", "in_thesis", "enriched", "ranks") else v)
                        for k, v in pc.items() if k != "baseline_ranks"} for c, pc in per_cutoff.items()},
        "query_audit": audit,
        "runtime_seconds": round(time.monotonic() - t_start),
    }
    (HERE / "results.json").write_text(json.dumps(results, indent=1, default=str))
    log.info("headline: %s", json.dumps(results["headline"]))
    log.info("query audit passed: %s", audit["passed"])


if __name__ == "__main__":
    main()
