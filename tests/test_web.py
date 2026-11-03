"""Homepage collector: robots.txt is obeyed, pages are cached, team lines lead the excerpt."""

from __future__ import annotations

from datetime import UTC, date, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from radar.agents.evidence import evidence_pack
from radar.collectors.web import WebCollector, extract, pick_subpages
from radar.store.models import Company, RawSnapshot, WebPage

HOME = """<html><head><title>Example Robotics</title><style>.x{}</style></head><body>
<nav><a href="/about">About</a> <a href="/team">Our team</a> <a href="/careers">Careers</a>
<a href="/private/plans">Plans</a> <a href="https://other.com/team">Elsewhere</a></nav>
<h1>Robot arms for factories</h1><p>We build low-cost arms for machine tending.</p>
<script>var tracking = 1;</script></body></html>"""
TEAM = """<html><head><title>Team</title></head><body><p>Our office is in Austin.</p>
<p>Jane Doe, co-founder and CEO, previously led controls at a robotics company.</p>
<p>John Roe, co-founder and CTO, PhD in mechanical engineering.</p></body></html>"""
ROBOTS = "User-agent: *\nDisallow: /careers\n"


def handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    html = {"/": HOME, "/team": TEAM, "/about": "<html><body><p>About Example Robotics: our mission is affordable automation for small machine shops.</p></body></html>"}
    if path == "/robots.txt":
        return httpx.Response(200, text=ROBOTS)
    if path in html:
        return httpx.Response(200, text=html[path], headers={"content-type": "text/html; charset=utf-8"})
    return httpx.Response(404)


def collector(session: Session, h=handler) -> WebCollector:  # type: ignore[no-untyped-def]
    return WebCollector(session, client=httpx.Client(transport=httpx.MockTransport(h)), offline=False, min_interval=0)


def test_extract_skips_scripts_and_finds_links() -> None:
    title, text, links = extract(HOME)
    assert title == "Example Robotics" and "machine tending" in text and "tracking" not in text
    sub = pick_subpages("https://example.ai/", "example.ai", links)
    assert sub == {"about": "https://example.ai/about", "team": "https://example.ai/team",
                   "careers": "https://example.ai/careers"}


def test_robots_is_obeyed_and_pages_cached(session: Session) -> None:
    pages = collector(session).collect_site("example.ai")
    kinds = {p.page_kind for p in pages}
    assert kinds == {"home", "about", "team"}  # /careers is disallowed by robots.txt
    assert all(p.snapshot_id for p in pages)
    snap = session.get(RawSnapshot, pages[0].snapshot_id)
    assert snap is not None and len(snap.content_sha256) == 64


def test_forbidden_robots_means_no_crawl(session: Session) -> None:
    def h(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403) if request.url.path == "/robots.txt" else handler(request)
    assert collector(session, h).collect_site("example.ai") == []
    assert session.scalar(select(WebPage.id)) is None


def test_team_lines_lead_the_evidence_excerpt(session: Session) -> None:
    collector(session).collect_site("example.ai")
    c = Company(entity_key="domain:example.ai", name="Example Robotics", kind="org", domain="example.ai",
                first_seen_at=datetime(2026, 1, 1, tzinfo=UTC))
    session.add(c)
    session.flush()
    items = [i for i in evidence_pack(session, c, date.today()) if i.kind.startswith("Company website")]
    team = next(i for i in items if i.kind == "Company website (team)")
    assert team.excerpt.startswith("Jane Doe, co-founder and CEO")
    assert items[0].kind == "Company website (team)"


def test_dead_domain_is_skipped_quickly(session: Session, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A company domain that does not resolve must not stall a run for the network patience."""
    import time

    from tenacity import wait_none

    from radar.collectors.base import HttpCollector

    monkeypatch.setattr(HttpCollector._send.retry, "wait", wait_none())  # type: ignore[attr-defined]
    monkeypatch.setenv("RADAR_NETWORK_PATIENCE_S", "900")

    def dead(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("[Errno 8] nodename nor servname provided, or not known")
    col = collector(session, dead)
    col.network_patience_s = 0.0
    t0 = time.monotonic()
    assert col.collect_site("no-such-company.example") == []
    assert time.monotonic() - t0 < 5
