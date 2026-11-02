"""Record small real API responses into tests/fixtures/<source>/ as cassettes.

Run once with network access: `uv run python scripts/record_fixtures.py [source ...]`.
Each call is deliberately tiny (a handful of rows) to keep fixtures readable. Auth headers
are never written; only status, a few response headers, and the body.
"""

from __future__ import annotations

import shutil
import sys
from datetime import date

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from radar.collectors.arxiv import ArxivCollector, build_query
from radar.collectors.ats import ATSCollector
from radar.collectors.base import FIXTURES, USER_AGENT, RecordingTransport
from radar.collectors.edgar import EdgarCollector
from radar.collectors.github import GitHubCollector
from radar.collectors.hn import HNCollector
from radar.collectors.openalex import OpenAlexCollector
from radar.collectors.sbir import SBIRCollector
from radar.collectors.semantic_scholar import SemanticScholarCollector
from radar.collectors.tranco import TrancoCollector
from radar.collectors.usaspending import CONTRACT_CODES, USAspendingCollector
from radar.store.models import Base

# Neutral, well-known public examples. None of these are Anti Fund portfolio companies.
GITHUB_REPO = "huggingface/lerobot"
ARXIV_ID = "2304.13705"  # ALOHA / ACT bimanual manipulation paper
ATS_BOARDS = [("greenhouse", "figureai"), ("lever", "shieldai"), ("ashby", "replit")]
TRANCO_DOMAIN = "ros.org"
EDGAR_DAY = date(2026, 9, 15)


def trim_job_boards(directory, keep: int = 3) -> None:
    """Job boards return every posting with full HTML descriptions (Lever: about 10 MB).
    Keep the first `keep` postings and drop long text fields so fixtures stay readable."""
    import json

    for path in directory.glob("*.json"):
        rec = json.loads(path.read_text())
        try:
            body = json.loads(rec["body"])
        except ValueError:
            continue
        jobs = body if isinstance(body, list) else body.get("jobs") if isinstance(body, dict) else None
        if not isinstance(jobs, list):
            continue
        slim = []
        for j in jobs[:keep]:
            slim.append({k: v for k, v in j.items()
                         if not (isinstance(v, str) and len(v) > 300) and k not in ("lists", "content")})
        body = slim if isinstance(body, list) else {**body, "jobs": slim}
        rec["body"] = json.dumps(body)
        rec["trimmed"] = f"first {keep} postings kept, long text fields dropped"
        path.write_text(json.dumps(rec, indent=1))


def client(source: str) -> httpx.Client:
    d = FIXTURES / source
    if d.exists():
        shutil.rmtree(d)
    return httpx.Client(transport=RecordingTransport(d), headers={"User-Agent": USER_AGENT},
                        timeout=60, follow_redirects=True)


def main(selected: list[str]) -> None:
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    s = Session(eng)
    run = lambda name: not selected or name in selected  # noqa: E731

    if run("github"):
        g = GitHubCollector(s, client=client("github"))
        g.search_repos("topic:imitation-learning created:2026-09-01..2026-09-30", max_pages=1, per_page=5)
        g.forks(GITHUB_REPO, max_pages=1)
        g.issues(GITHUB_REPO, max_pages=1)
        g.releases(GITHUB_REPO, max_pages=1)
        g.recent_stars(GITHUB_REPO, max_pages=1)
        g.repo_batch([GITHUB_REPO, "ros2/ros2"])
        g.commits(GITHUB_REPO, since=date(2026, 10, 1), until=date(2026, 10, 7), max_pages=1)
        g.contributors(GITHUB_REPO)
        g.owner("huggingface")
        g.languages(GITHUB_REPO)
    if run("arxiv"):
        a = ArxivCollector(s, client=client("arxiv"))
        a.search(build_query("cs.RO", "humanoid robot", date(2026, 10, 1), date(2026, 10, 7)), max_results=5, max_pages=1)
    if run("semantic_scholar"):
        sc = SemanticScholarCollector(s, client=client("semantic_scholar"))
        paper, _ = sc.paper_by_arxiv(ARXIV_ID)
        if paper and paper.get("authors"):
            aid = paper["authors"][0]["authorId"]
            sc.author(aid)
            sc.author_papers(aid, limit=10)
    if run("openalex"):
        o = OpenAlexCollector(s, client=client("openalex"))
        work, _ = o.work_by_arxiv(ARXIV_ID)
        if work and work.get("authorships"):
            o.author_works(work["authorships"][0]["author"]["id"].rsplit("/", 1)[-1], per_page=10)
    if run("hn"):
        h = HNCollector(s, client=client("hn"))
        h.search("humanoid robot", date(2026, 6, 1), date(2026, 10, 1), max_pages=1)
    if run("ats"):
        at = ATSCollector(s, client=client("ats"))
        for provider, slug in ATS_BOARDS:
            at.fetch_board(provider, slug)
        at.fetch_board("greenhouse", "this-board-does-not-exist-frontier-radar")
    if run("edgar"):
        e = EdgarCollector(s, client=client("edgar"))
        rows, _ = e.daily_index(EDGAR_DAY)
        form_d = [r for r in rows if r["form"] == "D"][:2]
        for r in form_d:
            e.primary_doc(r["cik"], r["path"].rsplit("/", 1)[-1].removesuffix(".txt"))
        e.search_form_d("robotics", date(2026, 9, 1), date(2026, 9, 30))
    if run("sbir"):
        sb = SBIRCollector(s, client=client("sbir"))
        sb.search("counter-UAS", agency="DOD", year=2025, rows=5, max_pages=1)
    if run("usaspending"):
        u = USAspendingCollector(s, client=client("usaspending"))
        u.search(["counter-UAS"], date(2026, 1, 1), date(2026, 9, 30),
                 award_codes=CONTRACT_CODES, limit=5, max_pages=1)
    if run("tranco"):
        t = TrancoCollector(s, client=client("tranco"))
        t.ranks(TRANCO_DOMAIN)
    s.commit()
    trim_job_boards(FIXTURES / "ats")
    for d in sorted(FIXTURES.iterdir()):
        if d.is_dir():
            print(f"{d.name}: {len(list(d.glob('*.json')))} cassettes")


if __name__ == "__main__":
    main(sys.argv[1:])
