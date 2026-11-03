"""Network resilience and resumable collection (no network: httpx.MockTransport)."""

from __future__ import annotations

from datetime import date
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from tenacity import wait_none

from radar.collectors.base import HttpCollector
from radar.collectors.github import GitHubCollector
from radar.store.checkpoints import Checkpoints
from radar.store.models import CollectionCheckpoint, Repo


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(HttpCollector._send.retry, "wait", wait_none())  # type: ignore[attr-defined]


class Flaky:
    """Fails with a DNS error for the first `fail` calls (or for URLs matching `fail_on`)."""

    def __init__(self, handler: Any, fail: int = 0, fail_on: str | None = None):
        self.handler, self.fail, self.fail_on, self.calls = handler, fail, fail_on, 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if self.calls <= self.fail or (self.fail_on and self.fail_on in str(request.url)):
            raise httpx.ConnectError("[Errno 8] nodename nor servname provided, or not known")
        return self.handler(request)


def collector(session: Session, transport: Flaky) -> GitHubCollector:
    return GitHubCollector(session, client=httpx.Client(transport=httpx.MockTransport(transport)),
                           offline=False, min_interval=0, token="")


def ok(_: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"ok": True})


def test_network_error_is_retried(session: Session) -> None:
    t = Flaky(ok, fail=3)
    f = collector(session, t).fetch("https://api.github.com/x")
    assert f.payload == {"ok": True} and t.calls == 4


def test_network_retries_outlast_http_attempts(session: Session) -> None:
    """HTTP errors stop after 6 attempts; a network outage is retried for longer."""
    t = Flaky(ok, fail=10)
    assert collector(session, t).fetch("https://api.github.com/x").payload == {"ok": True}


def test_network_patience_is_bounded(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RADAR_NETWORK_PATIENCE_S", "0")
    t = Flaky(ok, fail=100)
    with pytest.raises(httpx.ConnectError):
        collector(session, t).fetch("https://api.github.com/x")
    assert t.calls == 1


def search(request: httpx.Request) -> httpx.Response:
    """Fake search: 1,500 results for the full window (forces a split), 3 per half."""
    q = request.url.params["q"]
    window = q.split("created:")[1]
    if window == "2026-01-01..2026-01-31":
        return httpx.Response(200, json={"total_count": 1500, "items": []})
    items = [{"id": hash((q, i)) % 10**9, "full_name": f"org{i}/{q.split()[0][6:]}-{window}", "fork": False,
              "description": "robot arm", "created_at": "2026-01-10T00:00:00Z", "stargazers_count": 1,
              "owner": {"login": f"org{i}", "type": "Organization"}, "html_url": "https://github.com/x"}
             for i in range(3)]
    return httpx.Response(200, json={"total_count": 3, "items": items})


def test_exhaustive_search_resumes_from_checkpoints(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RADAR_NETWORK_PATIENCE_S", "0")
    from radar.collectors import github as gh
    monkeypatch.setattr(gh.GitHubCollector, "base_queries", staticmethod(lambda sector: ["topic:a", "topic:b"]))
    since, until = date(2026, 1, 1), date(2026, 1, 31)

    # First run: the network drops on the second half of the topic:b window.
    t1 = Flaky(search, fail_on="topic%3Ab+created%3A2026-01-17")
    with pytest.raises(httpx.ConnectError):
        collector(session, t1).discover_exhaustive("robotics", since, until, checkpoints=Checkpoints(session))
    session.rollback()  # what session_scope does on error: only the unfinished window is lost
    finished = Checkpoints(session).keys("github/robotics/")
    assert len(finished) == 5  # a split, a.1, a.2, b split, b.1
    repos_after_crash = session.scalar(select(func.count()).select_from(Repo))
    assert repos_after_crash == 9

    # Second run: only the unfinished window is fetched; stored counts include earlier work.
    t2 = Flaky(search)
    stored, truncated = collector(session, t2).discover_exhaustive("robotics", since, until,
                                                                   checkpoints=Checkpoints(session))
    assert t2.calls == 1
    assert stored == 12 and truncated == []
    assert session.scalar(select(func.count()).select_from(Repo)) == 12
    assert session.scalar(select(func.count()).select_from(CollectionCheckpoint)) == 6


def test_datetimes_read_back_timezone_aware(session: Session) -> None:
    """SQLite drops offsets; comparing a stored datetime with an aware one must still work."""
    from datetime import UTC, datetime

    session.add(CollectionCheckpoint(key="k", status="done", completed_at=datetime(2026, 1, 1, tzinfo=UTC)))
    session.commit()
    session.expire_all()
    cp = session.scalars(select(CollectionCheckpoint)).one()
    assert cp.completed_at.tzinfo is not None
    assert cp.completed_at < datetime(2026, 1, 2, tzinfo=UTC)
