"""Evidence packs: the exact cited text an agent may use, cut from raw snapshots.

An evidence item's id is the snapshot id. Its excerpt is re-derived from that snapshot's
raw payload (the matching repository in a search page, the matching arXiv entry, the
matching award row), never from derived tables, so a verifier reading the excerpt is
reading what the source returned. Excerpts carry the fetch date, so "120 stars" is always
"120 stars as of the fetch date".
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from radar.collectors.common import clean_text
from radar.store.models import (
    Award,
    Company,
    Filing,
    HNPost,
    Paper,
    PersonAffiliation,
    RawSnapshot,
    Repo,
    RepoEvent,
    RepoTree,
)

MAX_EXCERPT = 1200


@dataclass
class EvidenceItem:
    id: int            # snapshot id: the citation target
    kind: str
    title: str
    excerpt: str
    url: str | None
    fetched_at: str

    def render(self) -> str:
        return f"[{self.id}] {self.kind}: {self.title} (fetched {self.fetched_at})\n{self.excerpt}"


def _snap(session: Session, sid: int | None) -> RawSnapshot | None:
    return session.get(RawSnapshot, sid) if sid else None


def _fetched(s: RawSnapshot) -> str:
    return s.fetched_at.date().isoformat()


def _fields(d: dict[str, Any], keys: list[str]) -> str:
    out = []
    for k in keys:
        v = d.get(k)
        if v in (None, "", [], {}):
            continue
        if isinstance(v, list):
            v = ", ".join(str(x) for x in v[:12])
        out.append(f"{k}: {v}")
    return clean_text("; ".join(out))[:MAX_EXCERPT]


def _find(items: list[Any], key: str, value: Any) -> dict[str, Any] | None:
    for it in items or []:
        if isinstance(it, dict) and str(it.get(key, "")).lower() == str(value).lower():
            return it
    return None


def repo_item(session: Session, repo: Repo) -> EvidenceItem | None:
    s = _snap(session, repo.snapshot_id)
    if s is None:
        return None
    p = s.payload_json or {}
    item = _find(p.get("items", []), "full_name", repo.full_name)
    if item is None and isinstance(p.get("data"), dict):  # GraphQL batch
        item = next((v for v in p["data"].values() if isinstance(v, dict)
                     and str(v.get("nameWithOwner", "")).lower() == repo.full_name.lower()), None)
    if item is None:
        return None
    text = _fields(item, ["full_name", "nameWithOwner", "description", "created_at", "createdAt", "pushed_at",
                          "stargazers_count", "stargazerCount", "forks_count", "forkCount", "language", "topics",
                          "homepage", "homepageUrl"])
    return EvidenceItem(s.id, "GitHub repository", repo.full_name, text, f"https://github.com/{repo.full_name}", _fetched(s))


def tree_item(session: Session, tree: RepoTree) -> EvidenceItem | None:
    s = _snap(session, tree.snapshot_id)
    if s is None:
        return None
    paths = [t["path"] for t in (s.payload_json or {}).get("tree", []) if t.get("type") == "blob"]
    notable = [p for p in paths if p.lower().startswith((".github/workflows/", "tests/", "test/"))
               or p.lower().split("/")[-1].startswith(("readme", "license", "package.xml"))][:15]
    sig = tree.signals_json
    text = clean_text(f"commit {tree.sha[:10]} dated {tree.committed_at.date()}; {len(paths)} files; "
                      f"code files {sig.get('code_files')}, hardware-adjacent files {sig.get('hardware_files')}; "
                      f"README {sig.get('readme_bytes', 0)} bytes; notable paths: {', '.join(notable)}")[:MAX_EXCERPT]
    return EvidenceItem(s.id, "GitHub file tree", f"{tree.repo_full_name} at {tree.sha[:7]}", text,
                        f"https://github.com/{tree.repo_full_name}/tree/{tree.sha}", _fetched(s))


def event_items(session: Session, repo: str, kind: str, events: list[RepoEvent]) -> list[EvidenceItem]:
    by_snap: dict[int, list[RepoEvent]] = defaultdict(list)
    for e in events:
        if e.snapshot_id:
            by_snap[e.snapshot_id].append(e)
    out = []
    label = {"fork": "forks", "issue": "issues", "pr": "pull requests", "release": "releases",
             "commit": "commits", "star": "stars (Events API)"}[kind]
    for sid, evs in by_snap.items():
        s = _snap(session, sid)
        if s is None:
            continue
        evs.sort(key=lambda e: e.at)
        dates = [e.at.date().isoformat() for e in evs]
        ext = sum(1 for e in evs if e.is_external)
        text = (f"{len(evs)} {label} on this page, dated {dates[0]} to {dates[-1]}"
                + (f"; {ext} by people outside the owning organization" if kind in ("issue", "pr") else "")
                + f"; dates: {', '.join(dates[:40])}")
        out.append(EvidenceItem(s.id, f"GitHub {label}", repo, text[:MAX_EXCERPT], f"https://github.com/{repo}", _fetched(s)))
    return out


def paper_item(session: Session, paper: Paper) -> EvidenceItem | None:
    s = _snap(session, paper.snapshot_id)
    if s is None:
        return None
    from radar.collectors.arxiv import parse_feed

    entry = next((e for e in parse_feed((s.payload_json or {}).get("text", "")) if e["arxiv_id"] == paper.arxiv_id), None)
    if entry is None:
        return None
    authors = "; ".join(a["name"] + (f" ({', '.join(a['affiliations'])})" if a["affiliations"] else "")
                        for a in entry["authors"][:12])
    text = clean_text(f"title: {entry['title']}; submitted: {entry['submitted_at'][:10]}; categories: "
                      f"{', '.join(entry['categories'])}; authors: {authors}; abstract: {entry['abstract']}")[:MAX_EXCERPT]
    return EvidenceItem(s.id, "arXiv paper", paper.arxiv_id, text, f"https://arxiv.org/abs/{paper.arxiv_id}", _fetched(s))


def hn_item(session: Session, post: HNPost) -> EvidenceItem | None:
    s = _snap(session, post.snapshot_id)
    hit = _find((s.payload_json or {}).get("hits", []), "objectID", post.object_id) if s else None
    if s is None or hit is None:
        return None
    return EvidenceItem(s.id, "Hacker News post", post.title[:80],
                        _fields(hit, ["title", "url", "points", "num_comments", "author", "created_at"]),
                        f"https://news.ycombinator.com/item?id={post.object_id}", _fetched(s))


def award_item(session: Session, award: Award) -> EvidenceItem | None:
    s = _snap(session, award.snapshot_id)
    if s is None:
        return None
    results = (s.payload_json or {}).get("results", []) if isinstance(s.payload_json, dict) else s.payload_json
    row = _find(results, "generated_internal_id", award.external_id) or _find(results, "contract", award.external_id)
    if row is None:
        return None
    return EvidenceItem(s.id, "Federal award", award.recipient[:80],
                        _fields(row, ["Recipient Name", "Award Amount", "Start Date", "Awarding Agency",
                                      "Awarding Sub Agency", "Description", "firm", "award_amount",
                                      "proposal_award_date", "agency", "branch", "program", "phase", "award_title"]),
                        award.url, _fetched(s))


def filing_item(session: Session, filing: Filing) -> EvidenceItem | None:
    s = _snap(session, filing.snapshot_id)
    if s is None:
        return None
    text = clean_text(f"issuer: {filing.company_name}; form: {filing.form_type}; filed: {filing.filed_at}; "
                      f"industry: {filing.industry or 'not parsed'}; total offering: {filing.total_offering}; "
                      f"amount sold: {filing.amount_sold}")
    return EvidenceItem(s.id, "SEC Form D", filing.company_name, text, filing.url, _fetched(s))


def affiliation_items(session: Session, person_name: str, affs: list[PersonAffiliation]) -> list[EvidenceItem]:
    by_snap: dict[int, list[PersonAffiliation]] = defaultdict(list)
    for a in affs:
        if a.snapshot_id:
            by_snap[a.snapshot_id].append(a)
    out = []
    for sid, rows in by_snap.items():
        s = _snap(session, sid)
        if s is None:
            continue
        rows.sort(key=lambda a: a.at)
        text = clean_text(f"{person_name}: " + "; ".join(f"{a.at} {a.institution}" for a in rows[:30]))[:MAX_EXCERPT]
        out.append(EvidenceItem(s.id, "OpenAlex affiliations", person_name, text, None, _fetched(s)))
    return out


def evidence_pack(session: Session, company: Company, as_of: date, limit: int = 30) -> list[EvidenceItem]:
    """Evidence for one company, using only rows timestamped on or before as_of."""
    from radar.scoring.features import load_company_data

    d = load_company_data(session, company, as_of)
    items: list[EvidenceItem | None] = []
    items += [hn_item(session, p) for p in d.hn_posts]
    items += [repo_item(session, r) for r in d.repos[:6]]
    items += [paper_item(session, p) for p in d.papers[:6]]
    items += [tree_item(session, t) for t in d.trees[:4]]
    items += [award_item(session, a) for a in d.awards[:6]]
    items += [filing_item(session, f) for f in d.filings[:3]]
    for kind in ("fork", "issue", "pr", "release", "star"):
        evs = d.events.get(kind, [])
        for repo in sorted({e.repo_full_name for e in evs})[:3]:
            items += event_items(session, repo, kind, [e for e in evs if e.repo_full_name == repo])
    for p in d.persons:
        items += affiliation_items(session, p.name, d.affiliations.get(p.id, []))

    seen: dict[int, EvidenceItem] = {}
    for it in items:
        if it is None:
            continue
        if it.id in seen:  # one snapshot can hold several of this company's rows: merge excerpts
            prev = seen[it.id]
            if it.excerpt not in prev.excerpt:
                prev.excerpt = (prev.excerpt + " | " + it.excerpt)[:MAX_EXCERPT * 2]
            continue
        seen[it.id] = it
    return list(seen.values())[:limit]

