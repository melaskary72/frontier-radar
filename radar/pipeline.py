"""End-to-end scoring run for one as_of date.

    resolve -> graph -> paper code repos -> resolve again -> prescore -> enrich top N
    -> Form D lookups -> (live only) hiring and web rank -> score -> explain

Live mode (the weekly run) uses every feature. Backtest mode (`pit_only=True`) skips the
live-only enrichment and drops features that are not observable at as_of.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from radar.collectors.ats import ATSCollector
from radar.collectors.edgar import EdgarCollector
from radar.collectors.github import GitHubCollector
from radar.collectors.openalex import OpenAlexCollector
from radar.collectors.tranco import TrancoCollector
from radar.enrich import (
    Budget,
    enrich_company,
    enrich_consensus,
    enrich_live_only,
    materialize_paper_repos,
    prescore,
    thesis_probs,
)
from radar.entities.graph import build_graph
from radar.entities.resolve import is_incumbent, resolve
from radar.scoring.explain import explain
from radar.scoring.score import Scored, score_companies
from radar.store.models import Company

log = logging.getLogger(__name__)


@dataclass
class RunConfig:
    as_of: date
    pit_only: bool = False
    enrich_top: int = 150
    score_top: int = 600          # fully score the top of the prescore list
    github_budget: int = 3000
    other_budget: int = 600
    thesis_gate: float = 0.5      # companies below this thesis probability are not ranked
    include_labs: bool = False    # research groups stay in the graph, out of the ranking


@dataclass
class RunReport:
    as_of: date
    companies: int
    enriched: int
    scored: int
    requests: dict[str, int] = field(default_factory=dict)
    seconds: float = 0.0
    top: list[Scored] = field(default_factory=list)


def run(session: Session, cfg: RunConfig) -> RunReport:
    t0 = time.monotonic()
    resolve(session)
    build_graph(session)

    gh = GitHubCollector(session)
    gh_budget = Budget(cfg.github_budget)
    added = materialize_paper_repos(session, gh, gh_budget)
    if added:
        resolve(session)
        build_graph(session)

    companies = list(session.scalars(select(Company).where(Company.first_seen_at.is_not(None))))
    companies = [c for c in companies if c.first_seen_at.date() <= cfg.as_of
                 and c.kind != "stale" and (cfg.include_labs or c.kind != "lab")
                 and not is_incumbent(c.name, c.github_org)]
    probs = thesis_probs(session, companies)
    in_thesis = [c for c in companies if probs[c.id][0] >= cfg.thesis_gate]
    ranked = prescore(session, in_thesis, cfg.as_of, probs)
    chosen = [c for c, _ in ranked[: cfg.enrich_top]]

    oa = OpenAlexCollector(session)
    other = Budget(cfg.other_budget)
    for c in chosen:
        if gh_budget.left() <= 0:
            break
        enrich_company(session, gh, oa, c, cfg.as_of, gh_budget, live=not cfg.pit_only)
    edgar = EdgarCollector(session)
    enrich_consensus(session, edgar, chosen, cfg.as_of, other)
    ats = tranco = None
    if not cfg.pit_only:
        ats, tranco = ATSCollector(session), TrancoCollector(session)
        enrich_live_only(session, ats, tranco, chosen, other)
    session.flush()

    to_score = [c for c, _ in ranked[: max(cfg.score_top, cfg.enrich_top)]]
    scored = score_companies(session, to_score, cfg.as_of, pit_only=cfg.pit_only, thesis=probs)
    requests = {"github": gh.requests_made, "openalex": oa.requests_made, "edgar": edgar.requests_made}
    if ats and tranco:
        requests.update(ats=ats.requests_made, tranco=tranco.requests_made)
    log.info("%d companies, %d in thesis, %d enriched, %d scored", len(companies), len(in_thesis),
             len(chosen), len(scored))
    return RunReport(cfg.as_of, len(companies), len(chosen), len(scored), requests,
                     time.monotonic() - t0, scored[:25])


def describe(session: Session, report: RunReport, n: int = 10) -> str:
    lines = [f"as_of {report.as_of}: {report.companies} companies, {report.enriched} enriched, "
             f"{report.scored} scored in {report.seconds:.0f}s; requests {report.requests}"]
    for s in report.top[:n]:
        c = session.get(Company, s.company_id)
        ex = explain(s)
        top = "; ".join(e["label"][:70] for e in ex.top_evidence)
        lines.append(f"{s.rank:>3}. {s.total:.3f} {c.name if c else s.company_id} [{c.sector if c else '?'}] {top}")
    return "\n".join(lines)
