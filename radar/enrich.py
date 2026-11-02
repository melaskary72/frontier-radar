"""Enrichment: fetch the timelines that features need, for the companies worth the budget.

Stage 1 (`prescore`) ranks every resolved company using only signals that are safe at any
date T: thesis fit, how many distinct thesis queries surfaced it, whether it is an
organization rather than a personal account, and whether it has a launch post or a federal
award on or before T. No current star or fork counts are used, so the backtest's choice of
whom to enrich carries no hindsight.

Stage 2 (`enrich`) spends a hard request budget on the top of that list: forks, issues and
pull requests, releases, commits, the file tree at the last commit on or before T, owner
profiles, OpenAlex affiliation history for linked authors, and, in live mode only, recent
stars and job boards.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from radar.collectors.ats import ATSCollector
from radar.collectors.common import record_discovery
from radar.collectors.edgar import EdgarCollector
from radar.collectors.github import BudgetExhausted, GitHubCollector
from radar.collectors.openalex import OpenAlexCollector
from radar.collectors.tranco import TrancoCollector
from radar.entities.resolve import distinctive, strict_name
from radar.scoring.thesis import company_text, thesis_probability
from radar.store.models import (
    Award,
    Company,
    Discovery,
    Edge,
    HNPost,
    Paper,
    Person,
    PersonAffiliation,
    Repo,
    RepoEvent,
)

log = logging.getLogger(__name__)


@dataclass
class Budget:
    requests: int
    used: int = 0

    def left(self) -> int:
        return self.requests - self.used


def _links(session: Session, company_id: int) -> dict[str, list[int]]:
    out: dict[str, list[int]] = defaultdict(list)
    for e in session.scalars(select(Edge).where(Edge.src_type == "company", Edge.src_id == company_id)):
        out[e.dst_type].append(e.dst_id)
    return out


def materialize_paper_repos(session: Session, gh: GitHubCollector, budget: Budget) -> int:
    """Fetch metadata for repositories linked from paper abstracts but not yet stored.

    Each new repo inherits the paper's discovery rows, so its provenance is still a thesis
    query (the paper's), never a name lookup.
    """
    from radar.entities.graph import github_repos_in

    have = {n.lower() for n in session.scalars(select(Repo.full_name))}
    wanted: dict[str, Paper] = {}
    for paper in session.scalars(select(Paper)):
        for full in github_repos_in(paper.abstract):
            if full.lower() not in have:
                wanted.setdefault(full, paper)
    added = 0
    names = list(wanted)
    for i in range(0, len(names), 50):
        if budget.left() <= 0:
            break
        chunk = names[i:i + 50]
        before = gh.requests_made
        meta, snap = gh.repo_batch(chunk)
        budget.used += gh.requests_made - before
        for full, m in meta.items():
            if not m or m.get("isFork"):
                continue
            repo = gh.upsert_repo({
                "full_name": m["nameWithOwner"], "owner": {"login": m["owner"]["login"], "type":
                "Organization" if m["owner"]["__typename"] == "Organization" else "User"},
                "description": m.get("description"), "language": (m.get("primaryLanguage") or {}).get("name"),
                "created_at": m["createdAt"], "pushed_at": m.get("pushedAt"),
                "stargazers_count": m.get("stargazerCount", 0), "forks_count": m.get("forkCount", 0),
                "topics": [t["topic"]["name"] for t in (m.get("repositoryTopics") or {}).get("nodes", [])],
                "homepage": m.get("homepageUrl"),
            }, snap)
            paper = wanted[full]
            for d in session.scalars(select(Discovery).filter_by(table_name="papers", row_id=paper.id)):
                record_discovery(session, "repos", repo.id, d.sector, f"paper:{paper.arxiv_id}")
            added += 1
    session.flush()
    return added


def thesis_probs(session: Session, companies: list[Company], judge=None) -> dict[int, tuple[float, str]]:  # type: ignore[no-untyped-def]
    texts = [company_text(session, c) for c in companies]
    return {c.id: pm for c, pm in zip(companies, thesis_probability(texts, judge), strict=True)}


def prescore(session: Session, companies: list[Company], as_of: date,
             probs: dict[int, tuple[float, str]]) -> list[tuple[Company, float]]:
    """Cheap, hindsight-free ranking used only to decide whom to enrich."""
    t_end = datetime.combine(as_of, time.max, tzinfo=UTC)
    scored = []
    for c in companies:
        p = probs[c.id][0]
        links = _links(session, c.id)
        n_queries = 0
        for table in ("repos", "hn_posts", "awards", "papers"):
            ids = links.get(table, [])
            if ids:
                n_queries += session.scalar(select(func.count(func.distinct(Discovery.query))).where(
                    Discovery.table_name == table, Discovery.row_id.in_(ids))) or 0
        has_launch = bool(links.get("hn_posts")) and bool(session.scalar(select(func.count()).select_from(HNPost).where(
            HNPost.id.in_(links["hn_posts"]), HNPost.created_at <= t_end)))
        has_award = bool(links.get("awards")) and bool(session.scalar(select(func.count()).select_from(Award).where(
            Award.id.in_(links["awards"]), Award.award_date <= as_of)))
        s = (0.5 * p + 0.2 * min(n_queries, 5) / 5 + 0.15 * (c.kind in ("org", "launch", "awardee"))
             + 0.1 * has_launch + 0.05 * has_award)
        scored.append((c, s))
    return sorted(scored, key=lambda x: -x[1])


def enrich_company(session: Session, gh: GitHubCollector, oa: OpenAlexCollector | None, company: Company,
                   as_of: date, budget: Budget, *, live: bool, lookback_days: int = 365) -> None:
    links = _links(session, company.id)
    repos = list(session.scalars(select(Repo).where(Repo.id.in_(links.get("repos", [])))))
    since = as_of - timedelta(days=lookback_days)
    for repo in sorted(repos, key=lambda r: r.created_at)[:5]:
        if budget.left() <= 0:
            return
        before = gh.requests_made
        try:
            gh.forks(repo.full_name, max_pages=3)
            gh.issues(repo.full_name, max_pages=3)
            gh.releases(repo.full_name, max_pages=1)
            gh.commits(repo.full_name, since=since, until=as_of, max_pages=3)
            last = session.scalars(select(RepoEvent).where(
                RepoEvent.repo_full_name == repo.full_name, RepoEvent.kind == "commit",
                RepoEvent.at <= datetime.combine(as_of, time.max, tzinfo=UTC)).order_by(RepoEvent.at.desc())).first()
            if last is not None:
                gh.tree_at(repo.full_name, last.external_id, last.at)
            if live:
                gh.recent_stars(repo.full_name, max_pages=3)
        except BudgetExhausted:
            log.warning("GitHub budget exhausted during %s", repo.full_name)
            budget.used = budget.requests
            return
        budget.used += gh.requests_made - before

    if oa is not None:
        _enrich_authors(session, oa, links, as_of, budget)


def _enrich_authors(session: Session, oa: OpenAlexCollector, links: dict[str, list[int]], as_of: date,
                    budget: Budget, max_authors: int = 4) -> None:
    """Dated affiliations for the authors of papers the company's repositories implement."""
    repo_ids = links.get("repos", [])
    if not repo_ids:
        return
    paper_ids = list(session.scalars(select(Edge.dst_id).where(Edge.relation == "implements",
                                                               Edge.src_id.in_(repo_ids))))
    for paper in session.scalars(select(Paper).where(Paper.id.in_(paper_ids))):
        before = oa.requests_made
        work, _ = oa.work_by_arxiv(paper.arxiv_id)
        for authorship in (work or {}).get("authorships", [])[:max_authors]:
            author = authorship.get("author") or {}
            if not author.get("id"):
                continue
            aid = author["id"].rsplit("/", 1)[-1]
            person = session.scalars(select(Person).filter_by(openalex_id=aid)).first()
            if person is None:
                person = session.scalars(select(Person).filter_by(name=author.get("display_name", ""),
                                                                  openalex_id=None)).first()
                if person is None:
                    person = Person(name=author.get("display_name", aid), affiliations_json=[],
                                    first_seen_at=datetime.now(UTC))
                    session.add(person)
                person.openalex_id = aid
                session.flush()
            works, snap = oa.author_works(aid, until=as_of, per_page=50)
            for d, inst in OpenAlexCollector.affiliation_timeline(works, aid):
                exists = session.scalars(select(PersonAffiliation.id).filter_by(
                    person_id=person.id, at=d, institution=inst)).first()
                if exists is None:
                    session.add(PersonAffiliation(person_id=person.id, at=d, institution=inst, snapshot_id=snap))
        budget.used += oa.requests_made - before
        if budget.left() <= 0:
            return
    session.flush()


def enrich_consensus(session: Session, edgar: EdgarCollector, companies: list[Company], as_of: date,
                     budget: Budget) -> None:
    """Form D lookups by exact name for distinctive names: has consensus arrived yet?"""
    for c in companies:
        if budget.left() <= 0 or not distinctive(c.name):
            continue
        before = edgar.requests_made
        target = strict_name(c.name)
        for hit in edgar.search_form_d(target.title()):
            # Full-text search matches the phrase anywhere in a filing; keep only filings
            # whose issuer name is the company's name.
            if not (hit.get("filed_at") and hit.get("accession")) or strict_name(hit["company_name"]) != target:
                continue
            url = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={hit['cik']}&type=D"
            filing = edgar.store_filing(hit, None, url, hit["snapshot_id"])
            linked = session.scalars(select(Edge.id).filter_by(src_type="company", src_id=c.id,
                                                               dst_type="filings", dst_id=filing.id)).first()
            if linked is None:
                session.add(Edge(src_type="company", src_id=c.id, dst_type="filings", dst_id=filing.id,
                                 relation="has_evidence", confidence=0.9, evidence_snapshot_id=hit["snapshot_id"],
                                 method="deterministic"))
        budget.used += edgar.requests_made - before


def enrich_live_only(session: Session, ats: ATSCollector, tranco: TrancoCollector, companies: list[Company],
                     budget: Budget) -> None:
    """Hiring and web rank: current state only, never used by the backtest."""
    for c in companies:
        if budget.left() <= 0:
            return
        before = ats.requests_made + tranco.requests_made
        board = ats.find_board(c.name, c.domain)
        if board:
            ats.track(*board, company_id=c.id)
        if c.domain:
            tranco.ranks(c.domain)
        budget.used += ats.requests_made + tranco.requests_made - before
