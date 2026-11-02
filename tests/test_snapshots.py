from __future__ import annotations

from datetime import timedelta

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from radar.collectors.base import HttpCollector, OfflineMiss
from radar.store.models import RawSnapshot


class Dummy(HttpCollector):
    source = "hn"


def counting_client(body: bytes, status: int = 200) -> tuple[httpx.Client, list[int]]:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(status, content=body, headers={"content-type": "application/json"})

    return httpx.Client(transport=httpx.MockTransport(handler)), calls


def test_cache_hit_avoids_network(session: Session) -> None:
    client, calls = counting_client(b'{"a": 1}')
    c = Dummy(session, client=client, offline=False, min_interval=0)
    first = c.fetch("https://example.test/x", params={"q": "robot"})
    second = c.fetch("https://example.test/x", params={"q": "robot"})
    assert not first.from_cache and second.from_cache
    assert first.snapshot_id == second.snapshot_id and len(calls) == 1


def test_expired_cache_refetches_but_dedups_identical_content(session: Session) -> None:
    client, calls = counting_client(b'{"a": 1}')
    c = Dummy(session, client=client, offline=False, min_interval=0, ttl=timedelta(seconds=-1))
    c.fetch("https://example.test/y")
    c.fetch("https://example.test/y")
    assert len(calls) == 2
    assert session.scalar(select(func.count()).select_from(RawSnapshot)) == 1


def test_offline_mode_never_touches_network(session: Session) -> None:
    client, calls = counting_client(b"{}")
    c = Dummy(session, client=client, offline=True, min_interval=0)
    with pytest.raises(OfflineMiss):
        c.fetch("https://example.test/z")
    assert calls == []


def test_retry_on_server_error(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(HttpCollector._send.retry, "wait", lambda state: 0)  # type: ignore[attr-defined]
    statuses = iter([503, 429, 200])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(next(statuses), content=b'{"ok": true}')

    c = Dummy(session, client=httpx.Client(transport=httpx.MockTransport(handler)),
              offline=False, min_interval=0)
    f = c.fetch("https://example.test/retry")
    assert f.payload == {"ok": True} and c.requests_made == 3
