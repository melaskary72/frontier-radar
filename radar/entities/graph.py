"""Founder graph: repositories, papers, people, and companies.

Edges written here (all in `edges`, with the snapshot that justifies them):
  repo    --implements--> paper    arXiv id found in the repo's description or homepage
  person  --authored-->   paper    arXiv author list
  person  --owns-->       repo     user-owned repositories (the owner is the founder)
  company --has_author--> person   authors of papers the company's repositories implement
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from radar.store.models import Edge, Paper, Person, Repo

ARXIV_ID = re.compile(r"(?:arxiv\.org/(?:abs|pdf)/|arxiv:\s?)(\d{4}\.\d{4,5})", re.I)
GITHUB_REPO = re.compile(r"github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)", re.I)


def github_repos_in(text: str | None) -> list[str]:
    """owner/name pairs from GitHub URLs in free text (paper abstracts, comments)."""
    out = []
    for owner, name in GITHUB_REPO.findall(text or ""):
        name = name.rstrip(".").removesuffix(".git")
        if owner.lower() not in {"orgs", "topics", "features", "sponsors"} and name:
            out.append(f"{owner}/{name}")
    return list(dict.fromkeys(out))

GRAPH_RELATIONS = ("implements", "authored", "owns", "has_author")


def arxiv_ids_in(text: str | None) -> list[str]:
    return ARXIV_ID.findall(text or "")


def person_key(name: str) -> str:
    return " ".join(re.sub(r"[^a-z ]", " ", name.lower()).split())


def _person(session: Session, cache: dict[str, Person], name: str, *, login: str | None = None,
            first_seen: datetime | None = None) -> Person:
    if login:
        key = f"gh:{login.lower()}"
        if key not in cache:
            p = session.scalars(select(Person).filter_by(github_login=login.lower())).first()
            if p is None:
                p = Person(name=name, github_login=login.lower(), affiliations_json=[],
                           first_seen_at=first_seen or datetime.now(UTC))
                session.add(p)
            cache[key] = p
        return cache[key]
    key = f"name:{person_key(name)}"
    if key not in cache:
        p = session.scalars(select(Person).filter_by(name=name, github_login=None)).first()
        if p is None:
            p = Person(name=name, affiliations_json=[], first_seen_at=first_seen or datetime.now(UTC))
            session.add(p)
        cache[key] = p
    return cache[key]


def build_graph(session: Session) -> dict[str, int]:
    """Rebuild graph edges from current rows. Idempotent; human edges are kept."""
    session.execute(delete(Edge).where(Edge.relation.in_(GRAPH_RELATIONS), Edge.method != "human"))
    counts: dict[str, int] = defaultdict(int)
    people: dict[str, Person] = {}

    papers = {p.arxiv_id: p for p in session.scalars(select(Paper))}
    for paper in papers.values():
        for a in paper.authors_json or []:
            if not a.get("name"):
                continue
            person = _person(session, people, a["name"], first_seen=paper.submitted_at)
            session.flush()
            session.add(Edge(src_type="person", src_id=person.id, dst_type="papers", dst_id=paper.id,
                             relation="authored", confidence=1.0, evidence_snapshot_id=paper.snapshot_id,
                             method="deterministic"))
            counts["authored"] += 1

    repos_by_name = {r.full_name.lower(): r for r in session.scalars(select(Repo))}
    for paper in papers.values():
        for full in github_repos_in(paper.abstract):
            code_repo = repos_by_name.get(full.lower())
            if code_repo is not None:
                session.add(Edge(src_type="repos", src_id=code_repo.id, dst_type="papers", dst_id=paper.id,
                                 relation="implements", confidence=1.0, evidence_snapshot_id=paper.snapshot_id,
                                 method="deterministic"))
                counts["implements"] += 1

    for repo in repos_by_name.values():
        for aid in set(arxiv_ids_in(f"{repo.description} {repo.homepage}")):
            implemented = papers.get(aid)
            if implemented is None:
                continue
            session.add(Edge(src_type="repos", src_id=repo.id, dst_type="papers", dst_id=implemented.id,
                             relation="implements", confidence=1.0, evidence_snapshot_id=repo.snapshot_id,
                             method="deterministic"))
            counts["implements"] += 1
        if repo.owner_type == "User":
            person = _person(session, people, repo.owner, login=repo.owner, first_seen=repo.created_at)
            session.flush()
            session.add(Edge(src_type="person", src_id=person.id, dst_type="repos", dst_id=repo.id,
                             relation="owns", confidence=1.0, evidence_snapshot_id=repo.snapshot_id,
                             method="deterministic"))
            counts["owns"] += 1
    session.flush()

    # company -> person through papers its repositories implement.
    company_repos = defaultdict(set)
    for e in session.scalars(select(Edge).where(Edge.src_type == "company", Edge.dst_type == "repos")):
        company_repos[e.src_id].add(e.dst_id)
    repo_papers = defaultdict(set)
    for e in session.scalars(select(Edge).where(Edge.relation == "implements")):
        repo_papers[e.src_id].add(e.dst_id)
    paper_authors = defaultdict(set)
    for e in session.scalars(select(Edge).where(Edge.relation == "authored")):
        paper_authors[e.dst_id].add(e.src_id)
    for cid, repo_ids in company_repos.items():
        persons = {pid for r in repo_ids for pap in repo_papers[r] for pid in paper_authors[pap]}
        for pid in persons:
            session.add(Edge(src_type="company", src_id=cid, dst_type="person", dst_id=pid,
                             relation="has_author", confidence=0.9, evidence_snapshot_id=None,
                             method="deterministic"))
            counts["has_author"] += 1
    session.flush()
    return dict(counts)
