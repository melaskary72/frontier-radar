"""Small parsing and upsert helpers shared by collectors."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from radar.store.models import Base


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    v = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y%m%d"):
        try:
            return datetime.strptime(value.strip()[:10], fmt).date()
        except ValueError:
            continue
    dt = parse_dt(value)
    return dt.date() if dt else None


def upsert[T: Base](session: Session, model: type[T], where: dict[str, Any], values: dict[str, Any]) -> T:
    """Insert or update a row identified by `where`. Returns the row."""
    stmt = select(model).filter_by(**where)
    row = session.scalars(stmt).first()
    if row is None:
        row = model(**where, **values)
        session.add(row)
    else:
        for k, v in values.items():
            setattr(row, k, v)
    session.flush()
    return row


EM_DASH = chr(0x2014)


def clean_text(text: str | None) -> str:
    """Normalize third-party text for storage and display: em dashes become commas (house
    style), whitespace collapses."""
    if not text:
        return ""
    return " ".join(text.replace(f" {EM_DASH} ", ", ").replace(EM_DASH, ", ").split())


def text_has_any(text: str, terms: list[str]) -> bool:
    low = text.lower()
    return any(t.lower() in low for t in terms)


def record_discovery(session: Session, table: str, row_id: int, sector: str, query: str) -> None:
    """Log which thesis query surfaced a row (the backtest query audit trail)."""
    from radar.store.models import Discovery

    exists = session.scalars(select(Discovery.id).filter_by(
        table_name=table, row_id=row_id, sector=sector, query=query)).first()
    if exists is None:
        session.add(Discovery(table_name=table, row_id=row_id, sector=sector, query=query,
                              discovered_at=datetime.now(UTC)))
