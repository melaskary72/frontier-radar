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

from radar.agents.evidence import evidence_pack
from radar.agents.judges import entity_judge, thesis_judge
from radar.agents.research import write_memo
from radar.agents.sourcing import SourcingAgent, SourcingInput
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
    agents: bool = True           # LLM judges, why-now lines, memos (live mode only)
    judge_limit: int = 300        # thesis-judge calls per run (uncertain band only)
    why_now_top: int = 25
    memo_top: int = 5


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
    use_agents = cfg.agents and not cfg.pit_only
    resolve(session, judge=entity_judge(session) if use_agents else None)
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
    if use_agents:
        probs = _judge_uncertain(session, companies, probs, cfg.judge_limit)
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
    if use_agents:
        _why_now(session, scored[: cfg.why_now_top], cfg.as_of)
        for s in scored[: cfg.memo_top]:
            company = session.get(Company, s.company_id)
            if company is not None:
                write_memo(session, company, cfg.as_of)
    requests = {"github": gh.requests_made, "openalex": oa.requests_made, "edgar": edgar.requests_made}
    if ats and tranco:
        requests.update(ats=ats.requests_made, tranco=tranco.requests_made)
    log.info("%d companies, %d in thesis, %d enriched, %d scored", len(companies), len(in_thesis),
             len(chosen), len(scored))
    return RunReport(cfg.as_of, len(companies), len(chosen), len(scored), requests,
                     time.monotonic() - t0, scored[:25])


def _judge_uncertain(session: Session, companies: list[Company], probs: dict[int, tuple[float, str]],
                     limit: int) -> dict[int, tuple[float, str]]:
    """Send classifier probabilities in the uncertain band to the Haiku thesis judge."""
    from radar.scoring.thesis import UNCERTAIN, company_text

    judge = thesis_judge(session)
    uncertain = sorted((c for c in companies if UNCERTAIN[0] < probs[c.id][0] < UNCERTAIN[1]),
                       key=lambda c: abs(probs[c.id][0] - 0.5))[:limit]
    for c in uncertain:
        probs[c.id] = (judge(company_text(session, c)), "llm")
    log.info("thesis judge: %d of %d companies in the uncertain band", len(uncertain), len(companies))
    return probs


def _why_now(session: Session, scored: list[Scored], as_of: date) -> None:
    from radar.store.models import Score

    agent = SourcingAgent(session)
    for s in scored:
        company = session.get(Company, s.company_id)
        if company is None:
            continue
        ex = explain(s)
        r = agent.run(SourcingInput(company.name, company.sector, s.total, s.groups,
                                    evidence_pack(session, company, as_of), ex.top_evidence))
        row = session.get(Score, (s.company_id, as_of))
        if row is not None:
            row.breakdown_json = {**row.breakdown_json, "why_now": r.output.why_now,
                                  "why_now_evidence": r.output.evidence_ids, "why_now_confidence": r.output.confidence,
                                  "why_now_status": r.status, "top_evidence": ex.top_evidence}
    session.flush()


def describe(session: Session, report: RunReport, n: int = 10) -> str:
    lines = [f"as_of {report.as_of}: {report.companies} companies, {report.enriched} enriched, "
             f"{report.scored} scored in {report.seconds:.0f}s; requests {report.requests}"]
    for s in report.top[:n]:
        c = session.get(Company, s.company_id)
        ex = explain(s)
        top = "; ".join(e["label"][:70] for e in ex.top_evidence)
        lines.append(f"{s.rank:>3}. {s.total:.3f} {c.name if c else s.company_id} [{c.sector if c else '?'}] {top}")
    return "\n".join(lines)
