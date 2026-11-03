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
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from radar.agents.evidence import evidence_pack
from radar.agents.judges import entity_judge, thesis_judge
from radar.agents.research import write_memo
from radar.agents.sourcing import SourcingAgent, SourcingInput
from radar.collectors.ats import ATSCollector
from radar.collectors.common import clean_model_prose
from radar.collectors.edgar import EdgarCollector
from radar.collectors.github import GitHubCollector
from radar.collectors.openalex import OpenAlexCollector
from radar.collectors.tranco import TrancoCollector
from radar.collectors.web import WebCollector
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
from radar.entities.resolve import classify_profile, is_incumbent, resolve
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
    web_budget: int = 400       # company homepages, separate so job boards and SEC cannot starve it
    thesis_gate: float = 0.5      # companies below this thesis probability are not ranked
    include_labs: bool = False    # research groups stay in the graph, out of the ranking
    agents: bool = True           # LLM judges, why-now lines, memos (live mode only)
    judge_limit: int = 300        # thesis-judge calls per run (uncertain band only)
    why_now_top: int = 25
    memo_top: int = 5
    lookback_days: int | None = None  # backtest: only entities first seen within this window
    wait_on_budget: bool = False      # long batch runs sleep through GitHub limits


@dataclass
class RunReport:
    as_of: date
    companies: int
    enriched: int
    scored: int
    requests: dict[str, int] = field(default_factory=dict)
    seconds: float = 0.0
    top: list[Scored] = field(default_factory=list)
    universe_ids: list[int] = field(default_factory=list)   # eligible before the thesis gate
    in_thesis_ids: list[int] = field(default_factory=list)
    enriched_ids: list[int] = field(default_factory=list)
    scored_all: list[Scored] = field(default_factory=list)


def run(session: Session, cfg: RunConfig) -> RunReport:
    t0 = time.monotonic()
    marks: dict[str, float] = {}

    def mark(stage: str) -> None:
        marks[stage] = round(time.monotonic() - t0 - sum(marks.values()), 1)

    use_agents = cfg.agents and not cfg.pit_only
    resolve(session, judge=entity_judge(session) if use_agents else None)
    build_graph(session)
    mark("resolve")

    gh = GitHubCollector(session, wait_on_budget=cfg.wait_on_budget)
    gh_budget = Budget(cfg.github_budget)
    added = materialize_paper_repos(session, gh, gh_budget)
    if added:
        resolve(session)
        build_graph(session)
    mark("paper_repos")

    companies = list(session.scalars(select(Company).where(Company.first_seen_at.is_not(None))))
    earliest = cfg.as_of - timedelta(days=cfg.lookback_days) if cfg.lookback_days else None
    companies = [c for c in companies if c.first_seen_at.date() <= cfg.as_of
                 and (earliest is None or c.first_seen_at.date() >= earliest)
                 and c.kind not in ("stale", "incumbent") and (cfg.include_labs or c.kind != "lab")
                 and not is_incumbent(c.name, c.github_org)]
    probs = thesis_probs(session, companies, cfg.as_of)
    mark("thesis")
    if use_agents:
        probs = _judge_uncertain(session, companies, probs, cfg.judge_limit, cfg.as_of)
    in_thesis = [c for c in companies if probs[c.id][0] >= cfg.thesis_gate]
    ranked = prescore(session, in_thesis, cfg.as_of, probs)
    if not cfg.pit_only:  # live only: profiles are current, so the backtest never reads them
        ranked = drop_labs_and_incumbents(session, gh, ranked, cfg.enrich_top * 3, gh_budget)
    chosen = [c for c, _ in ranked[: cfg.enrich_top]]
    mark("prescore")

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
        web, web_budget = WebCollector(session), Budget(cfg.web_budget)
        for c in chosen:  # homepages for memos: current pages, live only
            if c.domain and web_budget.left() > 0:
                before = web.requests_made
                web.collect_site(c.domain)
                web_budget.used += web.requests_made - before
    session.flush()
    mark("enrich")

    to_score = [c for c, _ in ranked[: max(cfg.score_top, cfg.enrich_top)]]
    scored = score_companies(session, to_score, cfg.as_of, pit_only=cfg.pit_only, thesis=probs)
    mark("score")
    if use_agents:
        _why_now(session, scored[: cfg.why_now_top], cfg.as_of)
        for s in scored[: cfg.memo_top]:
            company = session.get(Company, s.company_id)
            if company is not None:
                write_memo(session, company, cfg.as_of)
    requests = {"github": gh.requests_made, "openalex": oa.requests_made, "edgar": edgar.requests_made}
    if ats and tranco:
        requests.update(ats=ats.requests_made, tranco=tranco.requests_made)
    mark("agents")
    log.info("%d companies, %d in thesis, %d enriched, %d scored; stage seconds %s", len(companies),
             len(in_thesis), len(chosen), len(scored), marks)
    return RunReport(cfg.as_of, len(companies), len(chosen), len(scored), requests,
                     time.monotonic() - t0, scored[:25], [c.id for c in companies], [c.id for c in in_thesis],
                     [c.id for c in chosen], scored)


def drop_labs_and_incumbents(session: Session, gh: GitHubCollector, ranked: list[tuple[Company, float]],
                             limit: int, budget: Budget) -> list[tuple[Company, float]]:
    """Read the GitHub profile of each top candidate and drop university groups and large
    companies whose name and login did not give them away (cvg is ETH Zurich's Computer
    Vision and Geometry Group; prbonn is a University of Bonn lab). Sets company.kind."""
    kept: list[tuple[Company, float]] = []
    dropped = 0
    for i, (c, score) in enumerate(ranked):
        if i < limit and c.github_org and budget.left() > 0:
            before = gh.requests_made
            prof, _ = gh.profile(c.github_org, c.kind)
            budget.used += gh.requests_made - before
            tag = classify_profile(c.github_org, prof)
            if tag:
                c.kind = tag
                dropped += 1
                continue
        kept.append((c, score))
    if dropped:
        log.info("profile check: dropped %d labs and incumbents from the top %d", dropped, limit)
    return kept


def _judge_uncertain(session: Session, companies: list[Company], probs: dict[int, tuple[float, str]],
                     limit: int, as_of: date) -> dict[int, tuple[float, str]]:
    """Send classifier probabilities in the uncertain band to the Haiku thesis judge."""
    from radar.scoring.thesis import UNCERTAIN, company_text

    judge = thesis_judge(session)
    uncertain = sorted((c for c in companies if UNCERTAIN[0] < probs[c.id][0] < UNCERTAIN[1]),
                       key=lambda c: abs(probs[c.id][0] - 0.5))[:limit]
    for c in uncertain:
        probs[c.id] = (judge(company_text(session, c, as_of)), "llm")
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
            row.breakdown_json = {**row.breakdown_json, "why_now": clean_model_prose(r.output.why_now),
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
