"""Resumable collection: finished work is recorded in collection_checkpoints.

Each mark commits, together with every row and raw snapshot the work produced, so a crash
loses at most the unit in progress, and a rerun skips everything already stored.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from radar.store.models import CollectionCheckpoint


class Checkpoints:
    def __init__(self, session: Session):
        self.session = session

    def get(self, key: str) -> CollectionCheckpoint | None:
        return self.session.scalars(select(CollectionCheckpoint).filter_by(key=key)).first()

    def done(self, key: str) -> bool:
        return self.get(key) is not None

    def keys(self, prefix: str = "") -> set[str]:
        q = select(CollectionCheckpoint.key)
        if prefix:
            q = q.where(CollectionCheckpoint.key.startswith(prefix))
        return set(self.session.scalars(q))

    def mark(self, key: str, status: str = "done", result: Any = None) -> None:
        cp = self.get(key)
        if cp is None:
            cp = CollectionCheckpoint(key=key, status=status, completed_at=datetime.now(UTC))
            self.session.add(cp)
        cp.status, cp.result_json, cp.completed_at = status, result, datetime.now(UTC)
        self.session.commit()
