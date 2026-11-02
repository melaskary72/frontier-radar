"""Each collector replays real responses recorded by scripts/record_fixtures.py."""

from __future__ import annotations

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from radar.collectors.arxiv import ArxivCollector, build_query
from radar.collectors.ats import ATSCollector, slug_candidates
from radar.collectors.base import replay_client
from radar.collectors.edgar import EdgarCollector
from radar.collectors.github import GitHubCollector
from radar.collectors.hn import HNCollector
from radar.collectors.openalex import OpenAlexCollector
from radar.collectors.sbir import SBIRCollector
from radar.collectors.semantic_scholar import SemanticScholarCollector
from radar.collectors.tranco import TrancoCollector
from radar.collectors.usaspending import CONTRACT_CODES, USAspendingCollector
from radar.store.models import RawSnapshot, RepoEvent

REPO = "huggingface/lerobot"
ARXIV_ID = "2304.13705"


def make(cls, session: Session, source: str, **kw):
    return cls(session, client=replay_client(source), offline=False, min_interval=0, **kw)


def test_github_search_and_timelines(session: Session) -> None:
    g = make(GitHubCollector, session, "github", token="")
    hits = g.search_repos("topic:imitation-learning created:2026-09-01..2026-09-30",
                          max_pages=1, per_page=5)
    assert hits, "search returned no repositories"
    repo = g.upsert_repo(*hits[0])
    assert repo.created_at.date() >= date(2026, 9, 1)

    assert g.forks(REPO, max_pages=1) > 0
    assert g.issues(REPO, max_pages=1) > 0
    assert g.releases(REPO, max_pages=1) > 0
    assert g.commits(REPO, since=date(2026, 10, 1), until=date(2026, 10, 7), max_pages=1) > 0
    assert g.recent_stars(REPO, max_pages=1) >= 1  # WatchEvents, live radar only

    kinds = dict(session.execute(
        select(RepoEvent.kind, func.count()).where(RepoEvent.repo_full_name == REPO)
        .group_by(RepoEvent.kind)).all())
    assert {"fork", "release", "commit"} <= set(kinds)
    assert "issue" in kinds or "pr" in kinds
    # Every event row is traceable to a stored raw response.
    orphans = session.scalar(select(func.count()).select_from(RepoEvent)
                             .where(RepoEvent.snapshot_id.is_(None)))
    assert orphans == 0

    meta, _ = g.repo_batch([REPO, "ros2/ros2"])
    assert meta[REPO]["stargazerCount"] > 0
    assert meta["ros2/ros2"]["owner"]["login"] == "ros2"

    owner, _ = g.owner("huggingface")
    assert owner and owner["type"] == "Organization"


def test_github_queries_are_thesis_only() -> None:
    from radar.config import thesis
    g = GitHubCollector.__new__(GitHubCollector)
    qs = GitHubCollector.queries_for_sector(g, "robotics", date(2026, 1, 1), date(2026, 3, 1))
    s = thesis().sectors["robotics"]
    assert len(qs) == len(s.github_topics) + len(s.keywords)
    assert all("created:2026-01-01..2026-03-01" in q for q in qs)


def test_arxiv(session: Session) -> None:
    a = make(ArxivCollector, session, "arxiv")
    rows = a.search(build_query("cs.RO", "humanoid robot", date(2026, 10, 1), date(2026, 10, 7)),
                    max_results=5, max_pages=1)
    assert rows
    paper = a.upsert_paper(*rows[0])
    assert paper.submitted_at.date() >= date(2026, 10, 1)
    assert paper.authors_json and paper.authors_json[0]["name"]
    assert any(c.startswith("cs.") for c in paper.categories_json)


def test_semantic_scholar(session: Session) -> None:
    sc = make(SemanticScholarCollector, session, "semantic_scholar")
    paper, _ = sc.paper_by_arxiv(ARXIV_ID)
    assert paper and paper["authors"]
    aid = paper["authors"][0]["authorId"]
    author, _ = sc.author(aid)
    assert author and author["hIndex"] >= 1
    papers, _ = sc.author_papers(aid, limit=10)
    assert sc.coauthor_counts(papers, aid)


def test_openalex_affiliation_timeline(session: Session) -> None:
    o = make(OpenAlexCollector, session, "openalex")
    work, _ = o.work_by_arxiv(ARXIV_ID)
    assert work and work["authorships"]
    author_id = work["authorships"][0]["author"]["id"].rsplit("/", 1)[-1]
    works, _ = o.author_works(author_id, per_page=10)
    timeline = o.affiliation_timeline(works, author_id)
    assert timeline and timeline == sorted(timeline)


def test_left_top_lab_logic() -> None:
    labs = ["Stanford University", "MIT"]
    tl = [(date(2023, 1, 1), "Stanford University"), (date(2025, 9, 1), "Acme Robotics Inc")]
    assert OpenAlexCollector.left_top_lab(tl, labs, as_of=date(2026, 1, 1)) == "Stanford University"
    # Before the move is visible, the signal must not fire (point-in-time safety).
    assert OpenAlexCollector.left_top_lab(tl, labs, as_of=date(2025, 6, 1)) is None
    # A move more than 18 months ago is stale.
    assert OpenAlexCollector.left_top_lab(tl, labs, as_of=date(2027, 6, 1)) is None
    # Word boundaries: "Smithsonian" must not match "MIT".
    assert OpenAlexCollector.left_top_lab([(date(2024, 1, 1), "Smithsonian"),
                                          (date(2025, 9, 1), "X")], labs, date(2026, 1, 1)) is None


def test_hn(session: Session) -> None:
    h = make(HNCollector, session, "hn")
    hits = h.search("humanoid robot", date(2026, 6, 1), date(2026, 10, 1), max_pages=1)
    assert hits
    assert all(date(2026, 6, 1) <= date.fromtimestamp(x["created_at_i"]) <= date(2026, 10, 1)
               for x, _ in hits)


def test_ats_boards(session: Session) -> None:
    at = make(ATSCollector, session, "ats")
    for provider, slug in [("greenhouse", "figureai"), ("lever", "shieldai"), ("ashby", "replit")]:
        jobs, _ = at.fetch_board(provider, slug)
        assert jobs, f"{provider}/{slug} returned nothing"
        assert all(j["external_id"] and j["title"] for j in jobs)
    missing, _ = at.fetch_board("greenhouse", "this-board-does-not-exist-frontier-radar")
    assert missing is None
    rows = at.track("greenhouse", "figureai")
    assert rows and all(r.first_seen_at == r.last_seen_at for r in rows)


def test_ats_slugs() -> None:
    assert slug_candidates("Acme Robotics", "acme.ai") == ["acmerobotics", "acme-robotics", "acme"]


def test_edgar_index_and_form_d(session: Session) -> None:
    e = make(EdgarCollector, session, "edgar")
    rows, snap = e.daily_index(date(2026, 9, 15))
    form_d = [r for r in rows if r["form"] == "D"]
    assert form_d
    r = form_d[0]
    acc = r["path"].rsplit("/", 1)[-1].removesuffix(".txt")
    detail, snap, url = e.primary_doc(r["cik"], acc)
    assert detail and detail["industry"] and detail["total_offering"] is not None
    filing = e.store_filing({**r, "accession": acc, "filed_at": r["date"]}, detail, url, snap)
    assert filing.filed_at == date(2026, 9, 15)
    hits = e.search_form_d("robotics", date(2026, 9, 1), date(2026, 9, 30))
    assert hits and all(h["form_type"].startswith("D") for h in hits)


def test_sbir_unavailable_is_graceful(session: Session) -> None:
    sb = make(SBIRCollector, session, "sbir")
    assert sb.search("counter-UAS", agency="DOD", year=2025, rows=5, max_pages=1) == []
    assert sb.available is False


def test_usaspending(session: Session) -> None:
    u = make(USAspendingCollector, session, "usaspending")
    rows = u.search(["counter-UAS"], date(2026, 1, 1), date(2026, 9, 30),
                    award_codes=CONTRACT_CODES, limit=5, max_pages=1)
    assert rows
    award = u.upsert_award(*rows[0])
    assert award.recipient and award.award_date is not None


def test_tranco_point_in_time(session: Session) -> None:
    t = make(TrancoCollector, session, "tranco")
    rows = t.ranks("ros.org")
    assert rows
    latest = max(r.list_date for r in rows)
    assert t.rank_as_of(rows, latest) is not None
    assert t.rank_as_of(rows, date(2000, 1, 1)) is None


def test_snapshots_have_provenance(session: Session) -> None:
    t = make(TrancoCollector, session, "tranco")
    t.ranks("ros.org")
    snap = session.scalars(select(RawSnapshot)).one()
    assert len(snap.content_sha256) == 64 and snap.fetched_at is not None
    assert snap.http_status == 200 and snap.source == "tranco"
