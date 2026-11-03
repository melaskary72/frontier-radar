"""Portfolio Stats: seed integrity, idempotent facts, and a fully cited LP update draft."""

from __future__ import annotations

import csv
import importlib.util
import json
from datetime import date
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from radar.store.models import PortfolioCompany, PortfolioFact

ROOT = Path(__file__).resolve().parents[1]
EM_DASH = chr(0x2014)


def load(name: str, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.setenv("DATABASE_URL", "sqlite://")  # never touch data/live.db from tests
    spec = importlib.util.spec_from_file_location(f"portfolio_{name}", ROOT / "portfolio" / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_seed_rows_are_verified() -> None:
    with open(ROOT / "portfolio" / "companies.csv", newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 34
    assert len({r["name"] for r in rows}) == 34
    for r in rows:
        assert r["source_url"].startswith("https://antifund.com")
        if r["domain"]:
            assert r["domain_source_url"], f"{r['name']}: domain without a verification source"
        assert bool(r["ats_provider"]) == bool(r["ats_slug"]), r["name"]
        if r["ats_slug"]:
            assert r["ats_provider"] in {"greenhouse", "lever", "ashby"}
            assert r["ats_verified_by"], f"{r['name']}: ATS board without verification"
        if r["github_org"]:
            assert r["github_verified_by"], f"{r['name']}: GitHub org without verification"
        assert EM_DASH not in json.dumps(r)


def test_facts_upsert_by_company_type_and_day(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    collect = load("collect", monkeypatch)
    c = PortfolioCompany(name="Example Co", source_url="https://antifund.com/#portfolio")
    session.add(c)
    session.flush()
    for n in (3, 5):
        collect.put_fact(session, c.id, "open_roles", {"n": n, "text": f"{n} roles"}, "https://x", date(2026, 11, 2))
    rows = list(session.scalars(select(PortfolioFact)))
    assert len(rows) == 1 and json.loads(rows[0].value)["n"] == 5


def test_lp_update_sentences_all_cite_facts(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    lp = load("lp_update", monkeypatch)
    today = date(2026, 11, 2)
    a = PortfolioCompany(name="Alpha", source_url="https://antifund.com/#portfolio")
    b = PortfolioCompany(name="Beta", source_url="https://antifund.com/#portfolio")
    session.add_all([a, b])
    session.flush()
    facts = [
        (a, "press_round", {"n": 12_000_000, "round": "Seed", "announced_on": "2026-06-01", "text": "x"}),
        (a, "open_roles", {"n": 40, "engineering": 12, "text": "x"}),
        (b, "open_roles", {"n": 0, "engineering": 0, "text": "x"}),
        (b, "tranco_rank", {"n": 54_321, "text": "x"}),
        (b, "github_pushed_90d", {"n": 0, "text": "x"}),
    ]
    for c, t, v in facts:
        session.add(PortfolioFact(company_id=c.id, fact_type=t, value=json.dumps(v), as_of=today,
                                  source_url=f"https://source/{t}"))
    session.flush()
    doc = lp.build(session)
    sentences = [x for sec in doc["sections"].values() for x in sec]
    assert sentences
    ids = set(session.scalars(select(PortfolioFact.id)))
    for x in sentences:
        assert x["citations"], x["text"]
        assert {c["fact_id"] for c in x["citations"]} <= ids
        assert EM_DASH not in x["text"]
    assert any("no open roles" in x["text"] for x in doc["sections"]["risks"])
    assert "Not Anti Fund's actual marks" in doc["label"]
    assert session.scalar(select(func.count()).select_from(PortfolioFact)) == len(facts)
