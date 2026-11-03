"""Agents with a scripted fake client: validation, retry, fallback, cap, trace, memo pipeline."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from radar.agents import cost
from radar.agents.base import strict_schema
from radar.agents.evidence import EvidenceItem
from radar.agents.research import SECTIONS, MemoDraft, write_memo
from radar.agents.sourcing import SourcingAgent, SourcingInput
from radar.agents.verifier import heuristic_verdict
from radar.store.models import AgentRun, Claim, Company, CostLog, Edge, HNPost, RawSnapshot

EM = chr(0x2014)


@pytest.fixture(autouse=True)
def tmp_ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("RADAR_COST_LEDGER", str(tmp_path / "ledger.jsonl"))
    monkeypatch.delenv("LLM_SPEND_CAP_USD", raising=False)
    yield


def response(payload: Any, stop: str = "end_turn", model: str = "claude-haiku-5-5") -> SimpleNamespace:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return SimpleNamespace(
        model=model, stop_reason=stop, stop_details=None,
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=1000, output_tokens=200, cache_read_input_tokens=0,
                              cache_creation_input_tokens=0),
    )


class FakeClient:
    def __init__(self, *responses: SimpleNamespace):
        self.queue = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.messages = SimpleNamespace(create=self._create)
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._beta_create))

    def _create(self, **kw: Any) -> SimpleNamespace:
        self.calls.append({"beta": False, **kw})
        return self.queue.pop(0)

    def _beta_create(self, **kw: Any) -> SimpleNamespace:
        self.calls.append({"beta": True, **kw})
        return self.queue.pop(0)


def ev(i: int, text: str) -> EvidenceItem:
    return EvidenceItem(i, "GitHub repository", f"item{i}", text, None, "2026-10-01")


def sourcing_input() -> SourcingInput:
    return SourcingInput("Kestrel", "space", 0.71, {"momentum": 0.2},
                         [ev(11, "kestrel/engine: 40 forks"), ev(12, "Launch HN: Kestrel")],
                         [{"label": "40 forks", "snapshot_id": 11}])


def test_strict_schema_is_closed_and_inlined() -> None:
    s = json.dumps(strict_schema(MemoDraft))
    assert "$ref" not in s and "$defs" not in s
    schema = strict_schema(MemoDraft)
    assert schema["additionalProperties"] is False and set(schema["required"]) == {"sections", "founder_call_questions"}


def test_ok_path_traces_and_logs_cost(session: Session) -> None:
    good = {"why_now": "Forty forks in a month. Launched on HN.", "sector": "space", "confidence": 0.7,
            "evidence_ids": [11, 12]}
    client = FakeClient(response(good))
    r = SourcingAgent(session, client=client).run(sourcing_input())
    assert r.status == "ok" and r.output.evidence_ids == [11, 12]
    run = session.get(AgentRun, r.run_id)
    assert run.prompt_version == "sourcing.v1" and run.input_tokens == 1000 and run.cost_usd > 0
    assert session.scalars(select(CostLog)).one().usd == pytest.approx(cost.price("claude-haiku-5-5", 1000, 200))
    assert client.calls[0]["output_config"]["format"]["type"] == "json_schema"
    assert client.calls[0]["beta"] is False  # Haiku: no server-side fallback


def test_invalid_output_retries_once_with_the_error(session: Session) -> None:
    bad = {"why_now": "x.", "sector": "space", "confidence": 0.5, "evidence_ids": [999]}
    good = {"why_now": "Forty forks.", "sector": "space", "confidence": 0.5, "evidence_ids": [11]}
    client = FakeClient(response(bad), response(good))
    r = SourcingAgent(session, client=client).run(sourcing_input())
    assert r.status == "retried"
    assert "999" in client.calls[1]["messages"][-1]["content"]


def test_two_failures_fall_back(session: Session) -> None:
    client = FakeClient(response("not json"), response("still not json"))
    r = SourcingAgent(session, client=client).run(sourcing_input())
    assert r.status == "fallback" and r.output.evidence_ids == [11]
    assert session.get(AgentRun, r.run_id).error


def test_refusal_falls_back(session: Session) -> None:
    r = SourcingAgent(session, client=FakeClient(response("", stop="refusal"))).run(sourcing_input())
    assert r.status == "fallback" and "refusal" in session.get(AgentRun, r.run_id).error


def test_spend_cap_blocks_the_call(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_SPEND_CAP_USD", "0.0001")
    client = FakeClient(response({}))
    r = SourcingAgent(session, client=client).run(sourcing_input())
    assert r.status == "capped" and client.calls == []


def test_ledger_survives_a_database_rebuild(session: Session) -> None:
    cost.log(session, "x", 2.0)
    from sqlalchemy import create_engine

    from radar.store.models import Base
    fresh = Session(create_engine("sqlite://"))
    Base.metadata.create_all(fresh.get_bind())
    assert cost.spent(fresh) == pytest.approx(2.0)


def test_no_client_uses_fallback(session: Session) -> None:
    r = SourcingAgent(session, client=None).run(sourcing_input())
    assert r.status == "fallback" and r.cost_usd == 0


def test_em_dashes_are_removed_from_model_output(session: Session) -> None:
    good = {"why_now": f"Forks rose {EM} fast.", "sector": "space", "confidence": 0.5, "evidence_ids": [11]}
    r = SourcingAgent(session, client=FakeClient(response(good))).run(sourcing_input())
    assert EM not in r.output.why_now


def test_heuristic_verifier() -> None:
    src = "kestrel/engine: created 2026-08-01; stargazers_count: 120; topics: propulsion"
    assert heuristic_verdict("Falcon Dynamics had 120 stars", src)[0] == "partial"  # names missing, number found
    assert heuristic_verdict("The repository had 120 stars as of 2026-08-01.", src)[0] == "supported"
    assert heuristic_verdict("The repository had 900 stars.", src)[0] == "unsupported"


def seed_company(session: Session) -> Company:
    snap = RawSnapshot(source="hn", url="u", request_key="k", fetched_at=datetime(2026, 10, 1, tzinfo=UTC),
                       content_sha256="0" * 64, http_status=200,
                       payload_json={"hits": [{"objectID": "77", "title": "Launch HN: Kestrel (YC S26) rotating detonation engines",
                                               "points": 120, "num_comments": 40, "created_at": "2026-09-20T10:00:00Z"}]})
    session.add(snap)
    session.flush()
    post = HNPost(object_id="77", kind="launch_hn", title="Launch HN: Kestrel (YC S26) rotating detonation engines",
                  points=120, num_comments=40, created_at=datetime(2026, 9, 20, 10, tzinfo=UTC), snapshot_id=snap.id)
    c = Company(entity_key="k", name="Kestrel", kind="launch", sector="space", first_seen_at=datetime(2026, 9, 20, tzinfo=UTC))
    session.add_all([post, c])
    session.flush()
    session.add(Edge(src_type="company", src_id=c.id, dst_type="hn_posts", dst_id=post.id, relation="has_evidence",
                     confidence=1, method="deterministic"))
    session.flush()
    return c


def substantive_analysis() -> dict[str, list[dict[str, Any]]]:
    def a(text: str) -> dict[str, Any]:
        return {"text": text, "kind": "analysis", "citations": []}
    return {
        "Technology": [a("Keeping a small turbine engine reliable under repeated thermal cycling is the hard problem here."),
                       a("The approach looks like a standard design packaged for a smaller airframe, not a new cycle.")],
        "Why now": [a("Cheaper additive manufacturing of hot-section parts makes small production runs economical today."),
                    a("Defense buyers now fund small attritable aircraft that need low-cost propulsion at volume.")],
        "Risks": [a("Engine durability under real duty cycles could fall short of what buyers require for fielding."),
                  a("Larger engine makers could offer a cheaper derivative and close the market within a few years."),
                  a("The public record names no team, so it is unclear who could certify an engine for flight.")],
    }


def test_memo_pipeline_removes_unsupported_claims(session: Session) -> None:
    c = seed_company(session)
    sid = session.scalars(select(RawSnapshot.id)).one()

    def sec(h: str, sents: list[dict[str, Any]]) -> dict[str, Any]:
        return {"heading": h, "sentences": sents}

    sections = {h: [] for h in SECTIONS}
    sections["Snapshot"] = [
        {"text": "Kestrel launched on Hacker News with 120 points.", "kind": "fact", "citations": [sid]},
        {"text": "Kestrel has three paying customers.", "kind": "fact", "citations": [sid]},
        {"text": "The launch drew 400 comments.", "kind": "fact", "citations": [sid]},
        {"text": "Kestrel raised a seed round.", "kind": "fact", "citations": []},
    ]
    sections.update(substantive_analysis())
    draft = {"sections": [sec(h, s) for h, s in sections.items()], "founder_call_questions": [f"Q{i}?" for i in range(9)]}
    verdicts = {"verdicts": [
        {"claim_id": 1, "verdict": "supported", "note": "points match", "rewrite": ""},
        {"claim_id": 2, "verdict": "unsupported", "note": "no customers mentioned", "rewrite": ""},
        {"claim_id": 3, "verdict": "partial", "note": "comment count wrong", "rewrite": "The launch drew 40 comments."},
    ]}
    client = FakeClient(response(draft, model="claude-sonnet-5-5"), response(verdicts, model="claude-sonnet-5-5"))
    pub = write_memo(session, c, date(2026, 10, 1), client=client)

    body = pub.memo.body_md
    assert "120 points" in body and f"[c:{sid}]" in body
    assert "paying customers" not in body and "seed round" not in body
    assert "40 comments" in body and "400 comments" not in body
    assert pub.summary["supported"] == 1 and pub.summary["unsupported"] == 1
    assert pub.summary["rewritten"] == 1 and pub.summary["uncited_dropped"] == 1
    assert all(call["beta"] and call["fallbacks"] == "default" for call in client.calls)  # Sonnet routing
    # The verifier saw only claims and excerpts, never the research prompt or draft.
    verifier_prompt = client.calls[1]["messages"][0]["content"]
    assert "Signals timeline" not in verifier_prompt and "founder" not in verifier_prompt.lower()
    assert {cl.verdict for cl in session.scalars(select(Claim))} == {"supported", "removed:unsupported", "partial"}


def test_repeated_facts_are_published_once(session: Session) -> None:
    """Spot-check finding: a memo stated the same creation date in two sections."""
    c = seed_company(session)
    sid = session.scalars(select(RawSnapshot.id)).one()
    sections: dict[str, list[dict[str, Any]]] = {h: [] for h in SECTIONS}
    fact = {"text": "Kestrel launched on Hacker News with 120 points.", "kind": "fact", "citations": [sid]}
    sections["Snapshot"] = [fact]
    sections["Signals timeline"] = [dict(fact, text="Kestrel launched on Hacker News with 120 points!")]
    sections.update(substantive_analysis())
    draft = {"sections": [{"heading": h, "sentences": v} for h, v in sections.items()],
             "founder_call_questions": [f"Q{i}?" for i in range(9)]}
    verdicts = {"verdicts": [{"claim_id": 1, "verdict": "supported", "note": "ok", "rewrite": ""}]}
    client = FakeClient(response(draft, model="claude-sonnet-5-5"), response(verdicts, model="claude-sonnet-5-5"))
    pub = write_memo(session, c, date(2026, 10, 1), client=client)
    assert pub.memo.body_md.count("120 points") == 1
    assert pub.summary["duplicates_dropped"] == 1 and pub.summary["checked"] == 1


def test_analysis_is_marked_and_cannot_smuggle_numbers(session: Session) -> None:
    c = seed_company(session)
    sid = session.scalars(select(RawSnapshot.id)).one()
    sections: dict[str, list[dict[str, Any]]] = {h: [] for h in SECTIONS}
    sections["Snapshot"] = [{"text": "Kestrel launched on Hacker News with 120 points.", "kind": "fact", "citations": [sid]}]
    sections.update(substantive_analysis())
    sections["Market"] = [{"text": "The market for these engines is worth 900 million dollars every year.",
                           "kind": "analysis", "citations": []}]
    draft = {"sections": [{"heading": h, "sentences": v} for h, v in sections.items()],
             "founder_call_questions": [f"Q{i}?" for i in range(9)]}
    verdicts = {"verdicts": [{"claim_id": 1, "verdict": "supported", "note": "ok", "rewrite": ""}]}
    client = FakeClient(response(draft, model="claude-sonnet-5-5"), response(verdicts, model="claude-sonnet-5-5"))
    pub = write_memo(session, c, date(2026, 10, 1), client=client)
    body = pub.memo.body_md
    assert "900 million" not in body and pub.summary["analysis_with_new_numbers_dropped"] == 1
    tech = body.split("## Technology")[1].split("## ")[0]
    assert "_Analysis:_ Keeping a small turbine engine" in tech


def test_thin_analysis_fails_the_structure_check() -> None:
    from radar.agents.research import ResearchAgent, ResearchInput

    sections = [{"heading": h, "sentences": []} for h in SECTIONS]
    draft = MemoDraft.model_validate({"sections": sections, "founder_call_questions": [f"Q{i}?" for i in range(9)]})
    problems = ResearchAgent.check(None, ResearchInput("x", None, date(2026, 1, 1), []), draft)  # type: ignore[arg-type]
    assert any("Risks needs at least 3" in p for p in problems)
    assert any("Why now needs at least 2" in p for p in problems)


def test_fact_contained_in_an_earlier_fact_is_dropped() -> None:
    """Spot-check #8/#25 pattern: the timeline restated a date already in the snapshot."""
    from radar.agents.research import dedupe

    def fact(text: str) -> dict[str, Any]:
        return {"text": text, "kind": "fact", "citations": [13]}
    sections = [{"heading": h, "sentences": []} for h in SECTIONS]
    sections[0]["sentences"] = [fact("The repository was created on 2026-09-19 and had 178 stars as of 2026-11-02.")]
    sections[5]["sentences"] = [fact("The repository was created on 2026-09-19."),
                                fact("Three releases were published between 2026-10-07 and 2026-10-22.")]
    draft = MemoDraft.model_validate({"sections": sections, "founder_call_questions": []})
    assert dedupe(draft) == 1
    assert [s.text for s in draft.sections[5].sentences] == ["Three releases were published between 2026-10-07 and 2026-10-22."]
