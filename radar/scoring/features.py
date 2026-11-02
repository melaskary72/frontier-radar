"""Point-in-time features for a company as of date T.

Every feature reads only rows whose own timestamp is on or before T. Features whose source
has no history (current citation counts, current job boards, Events API stars) are flagged
`pit=False`; the backtest runs with `pit_only=True` and excludes them entirely, so it never
sees a number that did not exist at T.

Each feature returns a raw value plus evidence items (snapshot id, label, URL, timestamp)
that explain.py surfaces as the "top evidence" chips on the dashboard.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from radar.config import sources, weights
from radar.store.models import (
    Award,
    Company,
    Edge,
    Filing,
    HNPost,
    JobPosting,
    Paper,
    Person,
    PersonAffiliation,
    Repo,
    RepoEvent,
    RepoTree,
)


@dataclass
class Evidence:
    snapshot_id: int | None
    label: str
    url: str | None
    at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"snapshot_id": self.snapshot_id, "label": self.label, "url": self.url, "at": self.at}


@dataclass
class FeatureValue:
    value: float | None
    evidence: list[Evidence] = field(default_factory=list)


@dataclass
class CompanyData:
    """Everything linked to one company, already filtered to timestamps on or before T."""

    company: Company
    as_of: date
    repos: list[Repo]
    events: dict[str, list[RepoEvent]]  # kind -> events
    trees: list[RepoTree]
    papers: list[Paper]
    persons: list[Person]
    affiliations: dict[int, list[PersonAffiliation]]
    hn_posts: list[HNPost]
    awards: list[Award]
    filings: list[Filing]
    jobs: list[JobPosting]

    @property
    def t_end(self) -> datetime:
        return datetime.combine(self.as_of, time.max, tzinfo=UTC)

    def window(self, kind: str, days: int) -> list[RepoEvent]:
        start = self.t_end - timedelta(days=days)
        return [e for e in self.events.get(kind, []) if start < _aware(e.at) <= self.t_end]


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _d(dt: datetime | date) -> date:
    return dt.date() if isinstance(dt, datetime) else dt


def gh(repo: str) -> str:
    return f"https://github.com/{repo}"


# --------------------------------------------------------------------------- loading

def load_company_data(session: Session, company: Company, as_of: date) -> CompanyData:
    t_end = datetime.combine(as_of, time.max, tzinfo=UTC)
    links: dict[str, list[int]] = defaultdict(list)
    for e in session.scalars(select(Edge).where(Edge.src_type == "company", Edge.src_id == company.id)):
        links[e.dst_type].append(e.dst_id)

    def rows(model: Any, table: str) -> list[Any]:
        ids = links.get(table, [])
        return list(session.scalars(select(model).where(model.id.in_(ids)))) if ids else []

    repos = [r for r in rows(Repo, "repos") if _aware(r.created_at) <= t_end]
    names = [r.full_name for r in repos]
    events: dict[str, list[RepoEvent]] = defaultdict(list)
    trees: list[RepoTree] = []
    if names:
        for ev in session.scalars(select(RepoEvent).where(RepoEvent.repo_full_name.in_(names),
                                                          RepoEvent.at <= t_end)):
            events[ev.kind].append(ev)
        latest: dict[str, RepoTree] = {}
        for tr in session.scalars(select(RepoTree).where(RepoTree.repo_full_name.in_(names),
                                                         RepoTree.committed_at <= t_end)):
            cur = latest.get(tr.repo_full_name)
            if cur is None or tr.committed_at > cur.committed_at:
                latest[tr.repo_full_name] = tr
        trees = list(latest.values())

    repo_ids = [r.id for r in repos]
    paper_ids = set()
    if repo_ids:
        paper_ids = set(session.scalars(select(Edge.dst_id).where(
            Edge.relation == "implements", Edge.src_type == "repos", Edge.src_id.in_(repo_ids))))
    papers = [p for p in (session.scalars(select(Paper).where(Paper.id.in_(paper_ids))) if paper_ids else [])
              if _aware(p.submitted_at) <= t_end]
    person_ids = links.get("person", [])
    persons = list(session.scalars(select(Person).where(Person.id.in_(person_ids)))) if person_ids else []
    affiliations: dict[int, list[PersonAffiliation]] = defaultdict(list)
    if person_ids:
        for a in session.scalars(select(PersonAffiliation).where(
                PersonAffiliation.person_id.in_(person_ids), PersonAffiliation.at <= as_of)):
            affiliations[a.person_id].append(a)

    return CompanyData(
        company=company, as_of=as_of, repos=repos, events=events, trees=trees, papers=papers,
        persons=persons, affiliations=affiliations,
        hn_posts=[p for p in rows(HNPost, "hn_posts") if _aware(p.created_at) <= t_end],
        awards=[a for a in rows(Award, "awards") if a.award_date and a.award_date <= as_of],
        filings=[f for f in rows(Filing, "filings") if f.filed_at <= as_of],
        jobs=[j for j in session.scalars(select(JobPosting).where(JobPosting.company_id == company.id))
              if _aware(j.first_seen_at) <= t_end],
    )


# --------------------------------------------------------------------------- features

def f_paper_count(d: CompanyData) -> FeatureValue:
    ev = [Evidence(p.snapshot_id, f"arXiv {p.arxiv_id}: {p.title[:80]}", f"https://arxiv.org/abs/{p.arxiv_id}",
                   _d(p.submitted_at).isoformat()) for p in sorted(d.papers, key=lambda p: p.submitted_at)[-3:]]
    return FeatureValue(math.log1p(len(d.papers)), ev)


def f_log_citations(d: CompanyData) -> FeatureValue:
    known = [p.citation_count for p in d.persons if p.citation_count is not None]
    return FeatureValue(math.log1p(sum(known)) if known else None)


def f_h_index(d: CompanyData) -> FeatureValue:
    known = [p.h_index for p in d.persons if p.h_index is not None]
    return FeatureValue(float(max(known)) if known else None)


def f_repo_quality(d: CompanyData) -> FeatureValue:
    if not d.trees:
        return FeatureValue(0.0)
    best, best_tree = 0.0, d.trees[0]
    for t in d.trees:
        s = t.signals_json
        q = (s.get("has_tests", False) + s.get("has_ci", False) + s.get("has_license", False)
             + min(s.get("readme_bytes", 0) / 3000, 1.0)) / 4
        if q > best:
            best, best_tree = q, t
    s = best_tree.signals_json
    label = (f"{best_tree.repo_full_name} at {best_tree.sha[:7]}: tests={s.get('has_tests')}, "
             f"CI={s.get('has_ci')}, license={s.get('has_license')}, README {s.get('readme_bytes', 0)} bytes")
    return FeatureValue(best, [Evidence(best_tree.snapshot_id, label, gh(best_tree.repo_full_name),
                                        _d(best_tree.committed_at).isoformat())])


def f_hardware_code_share(d: CompanyData) -> FeatureValue:
    code = sum(t.signals_json.get("code_files", 0) for t in d.trees)
    hw = sum(t.signals_json.get("hardware_files", 0) for t in d.trees)
    if code == 0:
        return FeatureValue(0.0)
    top = max(d.trees, key=lambda t: t.signals_json.get("hardware_files", 0))
    return FeatureValue(hw / code, [Evidence(top.snapshot_id, f"{hw} of {code} code files are C, C++, Rust, CUDA, HDL, or robot description",
                                             gh(top.repo_full_name), _d(top.committed_at).isoformat())])


def _velocity(d: CompanyData, kind: str, days: int, label: str, external_only: bool = False) -> FeatureValue:
    evs = d.window(kind, days)
    if external_only:
        evs = [e for e in evs if e.is_external]
    if not evs:
        return FeatureValue(0.0)
    by_repo = Counter(e.repo_full_name for e in evs)
    repo, n = by_repo.most_common(1)[0]
    snap = next(e.snapshot_id for e in evs if e.repo_full_name == repo)
    return FeatureValue(len(evs) / days, [Evidence(snap, f"{len(evs)} {label} in the {days} days before {d.as_of} ({repo}: {n})",
                                                   gh(repo), d.as_of.isoformat())])


def f_fork_velocity_30d(d: CompanyData) -> FeatureValue:
    return _velocity(d, "fork", 30, "forks")


def f_fork_velocity_90d(d: CompanyData) -> FeatureValue:
    return _velocity(d, "fork", 90, "forks")


def f_external_engagement_90d(d: CompanyData) -> FeatureValue:
    issues = _velocity(d, "issue", 90, "outside issues", external_only=True)
    prs = _velocity(d, "pr", 90, "outside pull requests", external_only=True)
    return FeatureValue((issues.value or 0) + (prs.value or 0), issues.evidence + prs.evidence)


def f_star_velocity_30d(d: CompanyData) -> FeatureValue:
    return _velocity(d, "star", 30, "new stars")


def f_contributor_growth(d: CompanyData) -> FeatureValue:
    """Commit authors active in the last 90 days who had never committed before then."""
    cutoff = d.t_end - timedelta(days=90)
    commits = d.events.get("commit", [])
    before = {e.actor for e in commits if e.actor and _aware(e.at) <= cutoff}
    recent = {e.actor for e in commits if e.actor and _aware(e.at) > cutoff}
    new = recent - before
    if not new:
        return FeatureValue(0.0)
    sample = next(e for e in commits if e.actor in new)
    return FeatureValue(float(len(new)), [Evidence(
        sample.snapshot_id, f"{len(new)} new commit authors in the 90 days before {d.as_of} ({len(recent)} active)",
        gh(sample.repo_full_name), d.as_of.isoformat())])


def f_commit_cadence(d: CompanyData) -> FeatureValue:
    evs = d.window("commit", 84)
    weeks = {_d(_aware(e.at)).isocalendar()[:2] for e in evs}
    if not weeks:
        return FeatureValue(0.0)
    return FeatureValue(len(weeks) / 12, [Evidence(evs[0].snapshot_id, f"commits in {len(weeks)} of the last 12 weeks",
                                                   gh(evs[0].repo_full_name), d.as_of.isoformat())])


def f_hiring_velocity(d: CompanyData) -> FeatureValue:
    if not d.jobs:
        return FeatureValue(None)
    eng = [j for j in d.jobs if j.is_engineering]
    hw = [j for j in d.jobs if j.is_hardware]
    j0 = d.jobs[0]
    return FeatureValue(math.log1p(len(eng)) + 0.5 * math.log1p(len(hw)),
                        [Evidence(j0.snapshot_id, f"{len(d.jobs)} open roles ({len(eng)} engineering, {len(hw)} hardware) on {j0.ats}",
                                  None, _d(j0.last_seen_at).isoformat())])


def f_left_top_lab_18m(d: CompanyData) -> FeatureValue:
    from radar.collectors.openalex import OpenAlexCollector

    labs = sources()["top_labs"]
    for p in d.persons:
        tl = sorted((a.at, a.institution) for a in d.affiliations.get(p.id, []))
        lab = OpenAlexCollector.left_top_lab(tl, labs, d.as_of)
        if lab:
            snap = next((a.snapshot_id for a in d.affiliations[p.id] if a.institution == lab), None)
            return FeatureValue(1.0, [Evidence(snap, f"{p.name} published under {lab}, then elsewhere within 18 months of {d.as_of}",
                                               None, d.as_of.isoformat())])
    return FeatureValue(0.0)


def f_repeat_collaborators(d: CompanyData) -> FeatureValue:
    pairs: Counter[tuple[str, str]] = Counter()
    for p in d.papers:
        names = sorted({a["name"] for a in p.authors_json or [] if a.get("name")})
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                pairs[(a, b)] += 1
    repeat = [(k, n) for k, n in pairs.items() if n >= 2]
    if not repeat:
        return FeatureValue(0.0)
    (a, b), n = max(repeat, key=lambda kv: kv[1])
    return FeatureValue(math.log1p(len(repeat)), [Evidence(d.papers[-1].snapshot_id, f"{a} and {b} co-authored {n} papers", None, None)])


def f_no_form_d(d: CompanyData) -> FeatureValue:
    if d.filings:
        f = min(d.filings, key=lambda f: f.filed_at)
        return FeatureValue(0.0, [Evidence(f.snapshot_id, f"Form D filed {f.filed_at} ({f.company_name})", f.url, f.filed_at.isoformat())])
    return FeatureValue(1.0)


def f_hn_points_below_threshold(d: CompanyData) -> FeatureValue:
    threshold = weights()["pre_consensus"]["hn_points_threshold"]
    if not d.hn_posts:
        return FeatureValue(1.0)
    top = max(d.hn_posts, key=lambda p: p.points)
    ev = [Evidence(top.snapshot_id, f"{top.title[:90]} ({top.points} points)",
                   f"https://news.ycombinator.com/item?id={top.object_id}", _d(top.created_at).isoformat())]
    return FeatureValue(1.0 if top.points < threshold else 0.0, ev)


def f_federal_award_present(d: CompanyData) -> FeatureValue:
    if not d.awards:
        return FeatureValue(0.0)
    a = max(d.awards, key=lambda a: a.amount or 0)
    amt = f"${a.amount:,.0f}" if a.amount else "amount not listed"
    return FeatureValue(1.0, [Evidence(a.snapshot_id, f"{a.agency or 'Federal'} {a.program or 'award'} {amt}: {(a.topic or '')[:70]}",
                                       a.url, a.award_date.isoformat() if a.award_date else None)])


@dataclass(frozen=True)
class FeatureSpec:
    name: str
    group: str
    fn: Callable[[CompanyData], FeatureValue]
    pit: bool  # observable point-in-time
    note: str = ""


# thesis_probability is computed by radar.scoring.thesis and merged in score.py.
FEATURES: list[FeatureSpec] = [
    FeatureSpec("paper_count", "technical_depth", f_paper_count, True),
    FeatureSpec("log_citations", "technical_depth", f_log_citations, False, "current Semantic Scholar totals"),
    FeatureSpec("h_index", "technical_depth", f_h_index, False, "current Semantic Scholar h-index"),
    FeatureSpec("repo_quality", "technical_depth", f_repo_quality, True, "tree at last commit before T"),
    FeatureSpec("hardware_code_share", "technical_depth", f_hardware_code_share, True),
    FeatureSpec("fork_velocity_30d", "momentum", f_fork_velocity_30d, True),
    FeatureSpec("fork_velocity_90d", "momentum", f_fork_velocity_90d, True),
    FeatureSpec("external_engagement_90d", "momentum", f_external_engagement_90d, True),
    FeatureSpec("star_velocity_30d", "momentum", f_star_velocity_30d, False, "Events API, last 90 days only"),
    FeatureSpec("contributor_growth", "momentum", f_contributor_growth, True),
    FeatureSpec("commit_cadence", "momentum", f_commit_cadence, True),
    FeatureSpec("hiring_velocity", "momentum", f_hiring_velocity, False, "job boards have no history"),
    FeatureSpec("left_top_lab_18m", "founder_signal", f_left_top_lab_18m, True),
    FeatureSpec("repeat_collaborators", "founder_signal", f_repeat_collaborators, True),
    FeatureSpec("no_form_d", "pre_consensus", f_no_form_d, True),
    FeatureSpec("hn_points_below_threshold", "pre_consensus", f_hn_points_below_threshold, True),
    FeatureSpec("federal_award_present", "federal_signal", f_federal_award_present, True),
]
UNAVAILABLE = {"prior_exit": "no public, point-in-time source for founder exits"}


def compute(d: CompanyData, *, pit_only: bool) -> dict[str, FeatureValue]:
    return {s.name: s.fn(d) for s in FEATURES if s.pit or not pit_only}
