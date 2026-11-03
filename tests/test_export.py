"""Dashboard export: JSON shape, memo citation mapping, and the no-em-dash house rule."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from radar.export import export
from radar.store.models import Company, Edge, Memo, PortfolioCompany, PortfolioFact, Repo, Score
from radar.store.snapshots import SnapshotStore, request_key

EM = chr(0x2014)


def _seed(session: Session) -> None:
    url = "https://api.github.com/search/repositories?q=topic:robotics"
    item = {"full_name": "acme/arm", "description": f"Robot arm {EM} open hardware", "stargazers_count": 42,
            "created_at": "2026-09-01T00:00:00Z"}
    snap = SnapshotStore(session).put(source="github", url=url, key=request_key("GET", url, None),
                                      raw=json.dumps({"items": [item]}).encode(), payload={"items": [item]}, http_status=200)
    repo = Repo(full_name="acme/arm", owner="acme", owner_type="Organization", description=item["description"],
                created_at=datetime(2026, 9, 1, tzinfo=UTC), stars=42, forks=3, topics_json=["robotics"],
                snapshot_id=snap.id)
    company = Company(entity_key="github:acme", kind="org", name="Acme", github_org="acme", sector="robotics",
                      first_seen_at=datetime(2026, 9, 1, tzinfo=UTC))
    session.add_all([repo, company])
    session.flush()
    session.add(Edge(src_type="company", src_id=company.id, dst_type="repos", dst_id=repo.id, relation="has_evidence",
                     confidence=1.0, evidence_snapshot_id=snap.id, method="deterministic"))
    session.add(Score(company_id=company.id, as_of=date(2026, 11, 1), total=0.5, rank=1, breakdown_json={
        "groups": {"technical_depth": 0.2, "momentum": 0.3}, "percentiles": {"repo_quality": 0.8}}))
    body = (f"# Acme\n\n_First-look memo._\n\n## Snapshot\n\nAcme has one repository, acme/arm. [c:{snap.id}] "
            f"It is early.\n\n## Founder-call questions\n\n1. Who builds it?\n2. Who buys it?\n")
    session.add(Memo(company_id=company.id, created_at=datetime(2026, 11, 1, tzinfo=UTC), version=1, body_md=body,
                     claims_json=[{"id": 1, "text": "Acme has one repository.", "published": "Acme has one repository, acme/arm.",
                                   "citations": [snap.id], "verdict": "supported"}],
                     verifier_summary_json={"supported": 1, "checked": 1, "supported_rate": 1.0}))
    pc = PortfolioCompany(name="Example Portfolio Co", source_url="https://example.com")
    session.add(pc)
    session.flush()
    session.add_all([PortfolioFact(company_id=pc.id, fact_type="open_roles", value=str(v), as_of=d,
                                   source_url="https://example.com/jobs")
                     for v, d in ((4, date(2026, 10, 1)), (6, date(2026, 11, 1)))])
    session.flush()


def test_export_writes_dashboard_json(session: Session, tmp_path: Path) -> None:
    _seed(session)
    counts = export(session, tmp_path, ledger=tmp_path / "missing.jsonl", backtest_dir=tmp_path)
    assert counts["companies"] == 1 and counts["memos"] == 1

    radar = json.loads((tmp_path / "radar.json").read_text())
    assert radar["as_of"] == "2026-11-01" and radar["rows"][0]["name"] == "Acme"
    assert radar["rows"][0]["groups"]["momentum"] == 0.3 and radar["rows"][0]["has_memo"]

    company = json.loads(next((tmp_path / "companies").glob("*.json")).read_text())
    memo = company["memo"]
    assert [s["heading"] for s in memo["sections"]] == ["Snapshot"]
    cited = [s for s in memo["sections"][0]["sentences"] if s["citations"]]
    assert cited and cited[0]["claim_id"] == 1 and cited[0]["verdict"] == "supported"
    snap = memo["snapshots"][str(cited[0]["citations"][0])]
    assert snap["sha256"] and snap["fetched_at"] and "acme/arm" in snap["excerpt"]
    assert memo["founder_call_questions"] == ["Who builds it?", "Who buys it?"]

    portfolio = json.loads((tmp_path / "portfolio.json").read_text())
    assert [p["value"] for p in portfolio["companies"][0]["facts"]["open_roles"]] == [4, 6]
    assert json.loads((tmp_path / "backtest.json").read_text())["status"] == "pending"

    for f in tmp_path.rglob("*.json"):
        assert EM not in f.read_text(), f"em dash in {f.name}"


def test_fact_values_use_the_collector_format() -> None:
    """portfolio/collect.py stores {"n": ..., "text": ...}; numeric facts export the number."""
    from radar.export import _fact_value

    raw = '{"n": 10, "engineering": 5, "text": "10 open roles (5 engineering)"}'
    assert _fact_value("open_roles", raw) == {"value": 10, "text": "10 open roles (5 engineering)"}
    raw = '{"n": 30000000, "text": "$30.0M Seed reported 2026-10-08"}'
    assert _fact_value("press_round", raw)["value"] == "$30.0M Seed reported 2026-10-08"
