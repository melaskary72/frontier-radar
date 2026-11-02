"""Engine and session helpers. SQLite by default, Postgres when DATABASE_URL is set."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from radar.config import REPO_ROOT
from radar.store.models import Base

DEFAULT_SQLITE = REPO_ROOT / "data" / "radar.db"


def database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        # Neon hands out postgres:// URLs; SQLAlchemy wants the psycopg driver name.
        return url.replace("postgres://", "postgresql+psycopg://", 1).replace(
            "postgresql://", "postgresql+psycopg://", 1
        )
    DEFAULT_SQLITE.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{DEFAULT_SQLITE}"


def make_engine(url: str | None = None) -> Engine:
    url = url or database_url()
    engine = create_engine(url, future=True)
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _):  # type: ignore[no-untyped-def]
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()

    return engine


_engine: Engine | None = None
_Session: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine, _Session
    if _engine is None:
        _engine = make_engine()
        _Session = sessionmaker(_engine, expire_on_commit=False)
    return _engine


@contextmanager
def session_scope(engine: Engine | None = None) -> Iterator[Session]:
    """Transactional scope: commit on success, roll back on error."""
    if engine is not None:
        factory = sessionmaker(engine, expire_on_commit=False)
    else:
        get_engine()
        assert _Session is not None
        factory = _Session
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def create_all(engine: Engine | None = None) -> None:
    """Create tables directly (tests and `make demo`). Production uses Alembic."""
    Base.metadata.create_all(engine or get_engine())
