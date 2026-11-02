"""Raw response cache. Every HTTP response is stored once with fetched_at and SHA-256.

Snapshots are the unit of provenance: features, edges, and memo claims cite snapshot ids,
and the dashboard opens the exact cached payload behind any citation.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from radar.store.models import RawSnapshot


def request_key(method: str, url: str, body: Any = None) -> str:
    body_s = "" if body is None else json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{method.upper()} {url}\n{body_s}".encode()).hexdigest()


def content_hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def now_utc() -> datetime:
    return datetime.now(UTC)


class SnapshotStore:
    def __init__(self, session: Session):
        self.session = session

    def latest(self, key: str, max_age: timedelta | None = None) -> RawSnapshot | None:
        stmt = (
            select(RawSnapshot)
            .where(RawSnapshot.request_key == key)
            .order_by(RawSnapshot.fetched_at.desc())
            .limit(1)
        )
        snap = self.session.scalars(stmt).first()
        if snap is None:
            return None
        if max_age is not None:
            fetched = snap.fetched_at
            if fetched.tzinfo is None:  # SQLite drops tzinfo; we always store UTC
                fetched = fetched.replace(tzinfo=UTC)
            if now_utc() - fetched > max_age:
                return None
        return snap

    def put(
        self, *, source: str, url: str, key: str, raw: bytes, payload: Any, http_status: int
    ) -> RawSnapshot:
        digest = content_hash(raw)
        # Identical content for the same request is not stored twice.
        prev = self.latest(key)
        if prev is not None and prev.content_sha256 == digest:
            return prev
        snap = RawSnapshot(
            source=source,
            url=url,
            request_key=key,
            fetched_at=now_utc(),
            content_sha256=digest,
            payload_json=payload,
            http_status=http_status,
        )
        self.session.add(snap)
        self.session.flush()
        return snap
