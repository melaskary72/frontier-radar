"""Private signals: relationship data a person enters, never collected automatically.

Warm intros, lab relationships, conference notes, and hiring notes are where a seed-stage edge
comes from (see the backtest analysis). This module takes them in from the CLI, a CSV, or the
dashboard form's local inbox, matches each to a company when it can, and stores the note as a
raw snapshot (source "private") so a memo can cite it exactly like a public source.

Private notes stay private: the database is gitignored, the inbox folder is gitignored, and the
static dashboard export withholds private excerpts.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from radar.agents.evidence import EvidenceItem
from radar.collectors.common import clean_text
from radar.entities.resolve import distinctive, normalize_domain, strict_name
from radar.store.models import Company, PrivateSignal, RawSnapshot
from radar.store.snapshots import SnapshotStore, request_key

KINDS = ("warm_intro", "lab_relationship", "conference_note", "hiring_note", "other")
CSV_COLUMNS = ["company_name", "domain", "kind", "person", "note", "source", "occurred_on"]
PRIVATE_SOURCE = "private"


class SignalError(ValueError):
    pass


def match_company(session: Session, company_name: str, domain: str | None) -> int | None:
    """Exact domain, then GitHub org, then exact distinctive name. Never fuzzy: a wrong match
    would attach someone's private note to the wrong company."""
    dom = normalize_domain(domain) if domain else None
    if dom:
        cid = session.scalar(select(Company.id).where(Company.domain == dom, Company.kind != "stale"))
        if cid:
            return cid
    org = company_name.strip().lower().replace(" ", "-")
    cid = session.scalar(select(Company.id).where(func.lower(Company.github_org) == org, Company.kind != "stale"))
    if cid:
        return cid
    if distinctive(company_name):
        key = strict_name(company_name)
        for c_id, name in session.execute(select(Company.id, Company.name).where(Company.kind != "stale")):
            if name and strict_name(name) == key:
                return int(c_id)
    return None


def add_signal(session: Session, *, company_name: str, kind: str, note: str, source: str, occurred_on: date,
               entered_by: str, person: str | None = None, domain: str | None = None) -> PrivateSignal:
    kind = kind.strip().lower()
    if kind not in KINDS:
        raise SignalError(f"kind must be one of {', '.join(KINDS)}; got {kind!r}")
    company_name, note, source = clean_text(company_name).strip(), clean_text(note).strip(), clean_text(source).strip()
    if not company_name or not note or not source:
        raise SignalError("company_name, note, and source are required")
    person = (clean_text(person).strip() or None) if person else None
    dom = normalize_domain(domain) if domain else None
    n = (session.scalar(select(func.max(PrivateSignal.id))) or 0) + 1
    url = f"private://signal/{n}"
    payload: dict[str, Any] = {"company_name": company_name, "domain": dom, "kind": kind, "person": person,
                               "note": note, "source": source, "occurred_on": occurred_on.isoformat(),
                               "entered_by": entered_by}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    snap = SnapshotStore(session).put(source=PRIVATE_SOURCE, url=url, key=request_key("PRIVATE", url),
                                      raw=raw, payload=payload, http_status=200)
    sig = PrivateSignal(company_id=match_company(session, company_name, dom), company_name=company_name, domain=dom,
                        kind=kind, person=person, note=note, source=source, occurred_on=occurred_on,
                        entered_by=entered_by, created_at=datetime.now(UTC), snapshot_id=snap.id)
    session.add(sig)
    session.flush()
    return sig


def _exists(session: Session, company_name: str, kind: str, note: str, occurred_on: date) -> bool:
    return session.scalar(select(PrivateSignal.id).where(
        PrivateSignal.company_name == clean_text(company_name).strip(), PrivateSignal.kind == kind.strip().lower(),
        PrivateSignal.note == clean_text(note).strip(), PrivateSignal.occurred_on == occurred_on)) is not None


@dataclass
class ImportResult:
    added: int
    skipped: int
    errors: list[str]


def _import_rows(session: Session, rows: list[dict[str, Any]], entered_by: str, label: str) -> ImportResult:
    res = ImportResult(0, 0, [])
    for i, r in enumerate(rows, 1):
        try:
            on = date.fromisoformat(str(r.get("occurred_on", "")).strip())
            if _exists(session, r.get("company_name", ""), r.get("kind", ""), r.get("note", ""), on):
                res.skipped += 1
                continue
            add_signal(session, company_name=r.get("company_name", ""), kind=r.get("kind", ""), note=r.get("note", ""),
                       source=r.get("source", ""), occurred_on=on, entered_by=r.get("entered_by") or entered_by,
                       person=r.get("person") or None, domain=r.get("domain") or None)
            res.added += 1
        except (SignalError, ValueError) as e:
            res.errors.append(f"{label} row {i}: {e}")
    return res


def import_csv(session: Session, path: Path, entered_by: str) -> ImportResult:
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return _import_rows(session, rows, entered_by, path.name)


def import_inbox(session: Session, path: Path, entered_by: str = "dashboard form") -> ImportResult:
    """Import the JSONL the dashboard form writes, then move processed lines to <path>.imported."""
    if not path.exists():
        return ImportResult(0, 0, [])
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for i, ln in enumerate(lines, 1):
        try:
            rows.append(json.loads(ln))
        except json.JSONDecodeError as e:
            errors.append(f"{path.name} line {i}: {e}")
    res = _import_rows(session, rows, entered_by, path.name)
    res.errors = errors + res.errors
    done = path.with_suffix(path.suffix + ".imported")
    with open(done, "a", encoding="utf-8") as f:
        f.writelines(ln + "\n" for ln in lines)
    path.write_text("", encoding="utf-8")
    return res


def private_signal_items(session: Session, company: Company, as_of: date) -> list[EvidenceItem]:
    """Evidence items for a memo: this company's signals dated on or before as_of."""
    out = []
    for s in session.scalars(select(PrivateSignal).where(PrivateSignal.company_id == company.id,
                                                         PrivateSignal.occurred_on <= as_of)
                             .order_by(PrivateSignal.occurred_on)):
        if s.snapshot_id is None:
            continue
        snap = session.get(RawSnapshot, s.snapshot_id)
        fetched = snap.fetched_at.date().isoformat() if snap else s.created_at.date().isoformat()
        excerpt = "; ".join(x for x in (f"person: {s.person}" if s.person else "", f"note: {s.note}") if x)
        out.append(EvidenceItem(id=s.snapshot_id, kind=f"Private signal ({s.kind})",
                                title=f"{s.kind} from {s.source}, {s.occurred_on.isoformat()}",
                                excerpt=clean_text(excerpt), url=None, fetched_at=fetched))
    return out
