"""Read side of agent tracing: what the Agent Ops page and the digest report.

Every agent call writes one `agent_runs` row in radar/agents/base.py. These helpers
summarize them: runs, status mix, cost, latency, and verifier pass rate over time.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from radar.store.models import AgentRun, Memo


def run_summary(session: Session) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = defaultdict(lambda: {"runs": 0, "cost_usd": 0.0, "latency_ms": [],
                                                         "status": defaultdict(int), "prompt_versions": set()})
    for r in session.scalars(select(AgentRun)):
        a = out[r.agent]
        a["runs"] += 1
        a["cost_usd"] += r.cost_usd or 0.0
        if r.latency_ms is not None:
            a["latency_ms"].append(r.latency_ms)
        a["status"][r.status] += 1
        a["prompt_versions"].add(r.prompt_version)
    for a in out.values():
        lat = sorted(a.pop("latency_ms"))
        a["p50_latency_ms"] = lat[len(lat) // 2] if lat else None
        a["status"] = dict(a["status"])
        a["prompt_versions"] = sorted(a["prompt_versions"])
    return dict(out)


def verifier_trend(session: Session) -> list[dict[str, Any]]:
    """Supported rate per memo, oldest first."""
    rows = []
    for m in session.scalars(select(Memo).order_by(Memo.created_at)):
        s = m.verifier_summary_json or {}
        rows.append({"memo_id": m.id, "company_id": m.company_id, "at": m.created_at.isoformat(),
                     "checked": s.get("checked", 0), "supported_rate": s.get("supported_rate")})
    return rows


def failures(session: Session, limit: int = 50) -> list[AgentRun]:
    return list(session.scalars(select(AgentRun).where(AgentRun.status != "ok")
                                .order_by(AgentRun.started_at.desc()).limit(limit)))
