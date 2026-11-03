"""Private signals: intake, matching, provenance, point-in-time evidence, export redaction.

All data here is synthetic (fictional companies, no real people)."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from radar.export import PRIVATE_WITHHELD, _snapshot
from radar.signals import SignalError, add_signal, import_csv, import_inbox, private_signal_items
from radar.store.models import Company, PrivateSignal, RawSnapshot


def company(session: Session, name: str, domain: str | None = None, org: str | None = None) -> Company:
    c = Company(entity_key=f"name:{name}", name=name, kind="org", domain=domain, github_org=org,
                first_seen_at=datetime(2026, 1, 1, tzinfo=UTC))
    session.add(c)
    session.flush()
    return c


def add(session: Session, **kw: object) -> PrivateSignal:
    base: dict[str, object] = {"company_name": "Example Robotics", "kind": "warm_intro", "note": "Intro via a portfolio founder.",
                               "source": "Partner A", "occurred_on": date(2026, 10, 1), "entered_by": "tester"}
    base.update(kw)
    return add_signal(session, **base)  # type: ignore[arg-type]


def test_add_creates_a_citable_private_snapshot(session: Session) -> None:
    sig = add(session)
    snap = session.get(RawSnapshot, sig.snapshot_id)
    assert snap is not None and snap.source == "private" and snap.url.startswith("private://signal/")
    raw = json.dumps(snap.payload_json, sort_keys=True, separators=(",", ":")).encode()
    assert snap.content_sha256 == hashlib.sha256(raw).hexdigest()
    assert snap.payload_json["note"] == "Intro via a portfolio founder."


def test_unknown_kind_and_missing_fields_are_rejected(session: Session) -> None:
    with pytest.raises(SignalError):
        add(session, kind="gossip")
    with pytest.raises(SignalError):
        add(session, note="  ")


def test_matching_domain_then_org_then_distinctive_name(session: Session) -> None:
    by_domain = company(session, "Example Robotics", domain="examplerobotics.com")
    by_org = company(session, "exampleorg", org="sample-dynamics")
    by_name = company(session, "Fictional Fusion Works")
    company(session, "Atlas")
    assert add(session, company_name="Anything", domain="https://www.examplerobotics.com/team").company_id == by_domain.id
    assert add(session, company_name="Sample Dynamics", note="n2").company_id == by_org.id
    assert add(session, company_name="Fictional Fusion Works, Inc.", note="n3").company_id == by_name.id
    assert add(session, company_name="Atlas", note="n4").company_id is None  # short names never match


def test_csv_import_is_idempotent(session: Session, tmp_path: Path) -> None:
    f = tmp_path / "signals.csv"
    f.write_text("company_name,domain,kind,person,note,source,occurred_on\n"
                 "Example Robotics,,warm_intro,,Met at a demo day.,Partner A,2026-09-01\n"
                 "Example Robotics,,conference_note,,Spoke on a panel.,Partner B,2026-09-02\n"
                 "Example Robotics,,bad_kind,,x,y,2026-09-03\n")
    first = import_csv(session, f, "tester")
    second = import_csv(session, f, "tester")
    assert (first.added, first.skipped, len(first.errors)) == (2, 0, 1)
    assert (second.added, second.skipped) == (0, 2)
    assert len(session.scalars(select(PrivateSignal)).all()) == 2


def test_inbox_import_moves_processed_lines(session: Session, tmp_path: Path) -> None:
    inbox = tmp_path / "private_signals.jsonl"
    inbox.write_text(json.dumps({"company_name": "Example Robotics", "kind": "hiring_note", "note": "Hiring a controls lead.",
                                 "source": "Recruiter note", "occurred_on": "2026-10-05"}) + "\n")
    res = import_inbox(session, inbox)
    assert res.added == 1 and inbox.read_text() == ""
    assert "controls lead" in (tmp_path / "private_signals.jsonl.imported").read_text()


def test_evidence_items_respect_as_of(session: Session) -> None:
    c = company(session, "Example Robotics", domain="examplerobotics.com")
    add(session, domain="examplerobotics.com", occurred_on=date(2026, 9, 1), note="Early intro.", person="A founder")
    add(session, domain="examplerobotics.com", occurred_on=date(2026, 12, 1), note="Later note.")
    items = private_signal_items(session, c, date(2026, 10, 1))
    assert len(items) == 1 and items[0].kind == "Private signal (warm_intro)"
    assert "Early intro." in items[0].excerpt and "person: A founder" in items[0].excerpt and items[0].url is None


def test_static_export_withholds_private_excerpts(session: Session) -> None:
    c = company(session, "Example Robotics", domain="examplerobotics.com")
    sig = add(session, domain="examplerobotics.com", note="Confidential detail.")
    items = {it.id: it for it in private_signal_items(session, c, date(2026, 12, 31))}
    assert sig.snapshot_id is not None
    out = _snapshot(session, sig.snapshot_id, items)
    assert out is not None and out["excerpt"] == PRIVATE_WITHHELD
    assert "Confidential" not in json.dumps(out) and out["url"] == ""
