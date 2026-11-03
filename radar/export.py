"""Export the dashboard's data as static JSON (web/public/data/).

The dashboard never talks to the database directly: this module reads the latest scored
run and writes plain JSON files that the Next.js app renders. Every number comes from the
database or the append-only cost ledger; every text field passes through clean_text (house
rule: no em dashes). Run: `uv run radar export` (see radar/cli.py).
"""

from __future__ import annotations

import json
import re
import shutil
from collections import Counter, defaultdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from radar.agents.evidence import EvidenceItem, evidence_pack
from radar.agents.research import ANALYSIS_MARK
from radar.collectors.common import clean_text
from radar.config import REPO_ROOT, weights
from radar.scoring.explain import explain
from radar.scoring.features import load_company_data
from radar.scoring.score import Scored
from radar.store.models import (
    AgentRun,
    Company,
    EntityCandidate,
    Feature,
    Memo,
    PipelineItem,
    PortfolioCompany,
    PortfolioFact,
    RawSnapshot,
    Score,
)

OUT_DIR = REPO_ROOT / "web" / "public" / "data"
PIPELINE_STAGES = ["Sourced", "Researching", "Founder Call", "Diligence", "IC", "Pass", "Invest"]
GROUP_LABELS = {
    "technical_depth": "Technical depth",
    "momentum": "Momentum",
    "founder_signal": "Founder signal",
    "thesis_fit": "Thesis fit",
    "pre_consensus": "Pre-consensus",
    "federal_signal": "Federal signal",
}
CITE = re.compile(r"((?:\s*\[c:\d+\])+)")
CITE_ID = re.compile(r"\[c:(\d+)\]")


def _t(x: Any) -> str:
    return clean_text(str(x)) if x is not None else ""


def _iso(x: date | datetime | None) -> str | None:
    if x is None:
        return None
    if isinstance(x, datetime):
        return (x if x.tzinfo else x.replace(tzinfo=UTC)).isoformat()
    return x.isoformat()


def _write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str))


def _scored(session: Session, s: Score) -> Scored:
    evidence: dict[str, list[dict[str, Any]]] = {}
    raw: dict[str, float | None] = {}
    for f in session.scalars(select(Feature).where(Feature.company_id == s.company_id, Feature.as_of == s.as_of)):
        evidence[f.name] = list(f.evidence_json or [])
        raw[f.name] = f.value
    b = s.breakdown_json or {}
    return Scored(s.company_id, s.total, s.rank, dict(b.get("groups", {})), dict(b.get("percentiles", {})), raw, evidence)


def _why_now(session: Session, ranked: list[tuple[Score, Company]]) -> dict[int, dict[str, Any]]:
    """Why-now lines: stored on the score row when present, otherwise matched from sourcing
    agent runs (run order follows rank order) only when the line names the company."""
    out: dict[int, dict[str, Any]] = {}
    for s, _c in ranked:
        b = s.breakdown_json or {}
        if b.get("why_now"):
            out[s.company_id] = {"text": _t(b["why_now"]), "status": b.get("why_now_status", "ok")}
    if out:
        return out
    runs = list(session.scalars(select(AgentRun).where(AgentRun.agent == "sourcing", AgentRun.status == "ok")
                                .order_by(AgentRun.id)))
    for run, (s, c) in zip(runs, ranked, strict=False):
        text = (run.output_json or {}).get("why_now") or ""
        name = c.name.lower().replace("-", " ")
        if text and (c.name.lower() in text.lower() or name in text.lower().replace("-", " ")):
            out[s.company_id] = {"text": _t(text), "status": "ok", "agent_run_id": run.id}
    return out


def _snapshot(session: Session, sid: int, items: dict[int, EvidenceItem]) -> dict[str, Any] | None:
    snap = session.get(RawSnapshot, sid)
    if snap is None:
        return None
    it = items.get(sid)
    return {
        "id": sid, "source": snap.source, "url": snap.url, "fetched_at": _iso(snap.fetched_at),
        "sha256": snap.content_sha256, "http_status": snap.http_status,
        "kind": _t(it.kind) if it else snap.source, "title": _t(it.title) if it else "",
        "link": it.url if it and it.url else snap.url,
        "excerpt": _t(it.excerpt) if it else "Raw response stored; no excerpt rule for this source.",
    }


def _memo(session: Session, memo: Memo, items: dict[int, EvidenceItem]) -> dict[str, Any]:
    claims = list(memo.claims_json or [])
    by_published = {re.sub(r"\s+", " ", _t(c.get("published") or "")).strip(): c for c in claims if c.get("published")}
    sections: list[dict[str, Any]] = []
    questions: list[str] = []
    body = "\n".join(_t(line) for line in (memo.body_md or "").split("\n"))  # keep line structure
    for block in re.split(r"\n(?=## )", body):
        if not block.startswith("## "):
            continue
        heading, _, text = block[3:].partition("\n")
        heading = heading.strip()
        if heading.lower().startswith("founder"):
            questions = [re.sub(r"^\d+\.\s*", "", ln).strip() for ln in text.splitlines() if re.match(r"^\d+\.", ln.strip())]
            continue
        sentences: list[dict[str, Any]] = []
        for para in [p for p in text.strip().split("\n\n") if p.strip()]:
            para = para.strip()
            kind = "fact"
            if para.startswith(ANALYSIS_MARK):  # research.v2 marks judgment paragraphs
                kind, para = "analysis", para[len(ANALYSIS_MARK):].strip()
            parts = CITE.split(para)
            for i in range(0, len(parts), 2):
                chunk = parts[i].strip()
                marks = parts[i + 1] if i + 1 < len(parts) else ""
                ids = [int(x) for x in CITE_ID.findall(marks)]
                pieces = [p for p in re.split(r"(?<=[.!?])\s+", chunk) if p]
                for j, piece in enumerate(pieces):
                    cited = ids if j == len(pieces) - 1 else []
                    claim = by_published.get(re.sub(r"\s+", " ", piece).strip()) if cited else None
                    sentences.append({
                        "text": piece, "citations": cited, "kind": kind,
                        "claim_id": claim.get("id") if claim else None,
                        "verdict": claim.get("verdict") if claim else None,
                        "note": _t(claim.get("note")) if claim and claim.get("note") else None,
                    })
            sentences.append({"text": "", "citations": [], "break": True})
        sections.append({"heading": heading, "sentences": sentences[:-1] if sentences else []})
    cited = sorted({c for sec in sections for s in sec["sentences"] for c in s["citations"]})
    snaps = {str(c): snap for c in cited if (snap := _snapshot(session, c, items))}
    removed = [{"text": _t(c.get("text")), "verdict": c.get("verdict"), "note": _t(c.get("note") or "")}
               for c in claims if str(c.get("verdict", "")).startswith("removed")]
    return {
        "id": memo.id, "version": memo.version, "created_at": _iso(memo.created_at),
        "intro": _t(body.split("\n## ")[0].split("\n", 1)[-1].strip().strip("_")),
        "sections": sections, "founder_call_questions": [_t(q) for q in questions],
        "snapshots": snaps, "verifier": memo.verifier_summary_json, "removed_claims": removed,
        "claims_total": len(claims),
    }


def _timeline(session: Session, company: Company, as_of: date) -> list[dict[str, Any]]:
    d = load_company_data(session, company, as_of)
    ev: list[dict[str, Any]] = []
    for r in d.repos:
        ev.append({"kind": "repo", "at": _iso(r.created_at), "label": f"Repository created: {_t(r.full_name)}"})
    weekly: dict[tuple[str, str], int] = Counter()
    for kind, events in d.events.items():
        for e in events:
            at = e.at if e.at.tzinfo else e.at.replace(tzinfo=UTC)
            iso = at.isocalendar()
            weekly[(kind, date.fromisocalendar(iso.year, iso.week, 1).isoformat())] += 1
    names = {"star": "stars", "fork": "forks", "release": "releases", "commit": "commits", "issue": "issues", "pr": "pull requests"}
    for (kind, wk), n in sorted(weekly.items()):
        ev.append({"kind": kind, "at": wk, "count": n, "label": f"{n} {names.get(kind, kind)} in week of {wk}"})
    for p in d.papers:
        ev.append({"kind": "paper", "at": _iso(p.submitted_at), "label": f"Paper submitted: {_t(p.title)[:120]}",
                   "url": f"https://arxiv.org/abs/{p.arxiv_id}"})
    for h in d.hn_posts:
        ev.append({"kind": "launch", "at": _iso(h.created_at), "label": f"Hacker News: {_t(h.title)[:120]}",
                   "url": f"https://news.ycombinator.com/item?id={h.object_id}"})
    for a in d.awards:
        ev.append({"kind": "award", "at": _iso(a.award_date), "label": f"Federal award: {_t(a.agency or '')} {_t(a.program or '')}".strip(),
                   "url": a.url})
    for f in d.filings:
        ev.append({"kind": "filing", "at": _iso(f.filed_at), "label": f"SEC Form {f.form_type} filed", "url": f.url})
    for j in d.jobs:
        ev.append({"kind": "hire", "at": _iso(j.first_seen_at), "label": f"Job posted: {_t(j.title)[:100]}"})
    for affs in d.affiliations.values():
        for af in affs:
            ev.append({"kind": "affiliation", "at": _iso(af.at), "label": f"Affiliation: {_t(af.institution)[:100]}"})
    return sorted((e for e in ev if e["at"]), key=lambda e: e["at"])


def _company(session: Session, s: Score, c: Company, why: dict[str, Any] | None) -> dict[str, Any]:
    pack = evidence_pack(session, c, s.as_of, limit=40)
    items = {it.id: it for it in pack}
    ex = explain(_scored(session, s), top_k=5)
    memo = session.scalars(select(Memo).where(Memo.company_id == c.id).order_by(Memo.version.desc())).first()
    snaps = {it.id: session.get(RawSnapshot, it.id) for it in pack}
    evidence = [{"snapshot_id": it.id, "kind": _t(it.kind), "title": _t(it.title), "excerpt": _t(it.excerpt),
                 "url": it.url, "fetched_at": it.fetched_at,
                 "sha256": sn.content_sha256 if (sn := snaps.get(it.id)) is not None else None} for it in pack]
    return {
        "id": c.id, "name": _t(c.name), "kind": c.kind, "sector": c.sector, "domain": c.domain,
        "github_org": c.github_org, "location": _t(c.location) if c.location else None,
        "first_seen_at": _iso(c.first_seen_at), "as_of": _iso(s.as_of), "rank": s.rank, "total": round(s.total, 4),
        "contributions": ex.contributions, "top_evidence": [{k: _t(v) if isinstance(v, str) else v for k, v in e.items()}
                                                           for e in ex.top_evidence],
        "why_now": why, "evidence": evidence, "timeline": _timeline(session, c, s.as_of),
        "memo": _memo(session, memo, items) if memo else None,
    }


def _agent_ops(sessions: list[tuple[str, Session]], ledger: Path) -> dict[str, Any]:
    runs: list[dict[str, Any]] = []
    for label, sess in sessions:
        for ar in sess.scalars(select(AgentRun).order_by(AgentRun.started_at)):
            runs.append({"db": label, "id": ar.id, "agent": ar.agent, "model": ar.model, "prompt_version": ar.prompt_version,
                         "started_at": _iso(ar.started_at), "latency_ms": ar.latency_ms, "input_tokens": ar.input_tokens,
                         "output_tokens": ar.output_tokens, "cost_usd": ar.cost_usd, "status": ar.status,
                         "error": _t(ar.error) if ar.error else None})
    runs.sort(key=lambda r: r["started_at"] or "")
    cost_points: list[dict[str, Any]] = []
    total = 0.0
    if ledger.exists():
        for line in ledger.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            total += float(row.get("usd", 0.0))
            cost_points.append({"at": row.get("at"), "agent": row.get("agent"), "usd": row.get("usd"),
                                "cumulative_usd": round(total, 6)})
    by_agent: dict[str, dict[str, Any]] = {}
    lat: dict[str, list[int]] = defaultdict(list)
    for r in runs:
        a = by_agent.setdefault(r["agent"], {"agent": r["agent"], "runs": 0, "ok": 0, "cost_usd": 0.0, "models": set()})
        a["runs"] += 1
        a["ok"] += 1 if r["status"] == "ok" else 0
        a["cost_usd"] += r["cost_usd"] or 0.0
        a["models"].add(r["model"])
        if r["latency_ms"]:
            lat[r["agent"]].append(r["latency_ms"])
    for name, a in by_agent.items():
        xs = sorted(lat[name])
        a["models"] = sorted(a["models"])
        a["cost_usd"] = round(a["cost_usd"], 6)
        a["latency_p50_ms"] = xs[len(xs) // 2] if xs else None
        a["latency_max_ms"] = xs[-1] if xs else None
    prompts = Counter((r["agent"], r["prompt_version"]) for r in runs)
    return {
        "runs": runs, "cost": cost_points, "ledger_total_usd": round(total, 6),
        "by_agent": sorted(by_agent.values(), key=lambda a: -a["runs"]),
        "prompt_versions": [{"agent": a, "version": v, "runs": n} for (a, v), n in sorted(prompts.items())],
        "failures": [r for r in runs if r["status"] != "ok"],
    }


NUMERIC_FACTS = {"open_roles", "github_stars", "github_pushed_90d", "tranco_rank", "spv_form_d_24m"}


def _fact_value(fact_type: str, raw: str) -> dict[str, Any]:
    """portfolio/collect.py stores values as JSON ({"n": ..., "text": ...}); show the number for
    numeric facts (so trends can be drawn) and the sentence for the rest."""
    try:
        v = json.loads(raw)
    except (TypeError, ValueError):
        return {"value": _t(raw), "text": _t(raw)}
    if not isinstance(v, dict):
        return {"value": v, "text": _t(str(v))}
    text = _t(str(v.get("text", "")))
    value = v.get("n") if fact_type in NUMERIC_FACTS and isinstance(v.get("n"), int | float) else text
    return {"value": value, "text": text}


def _portfolio(session: Session) -> dict[str, Any]:
    companies = list(session.scalars(select(PortfolioCompany).order_by(PortfolioCompany.name)))
    out: list[dict[str, Any]] = []
    for pc in companies:
        facts = list(session.scalars(select(PortfolioFact).where(PortfolioFact.company_id == pc.id).order_by(PortfolioFact.as_of)))
        series: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for f in facts:
            series[f.fact_type].append({"as_of": _iso(f.as_of), **_fact_value(f.fact_type, f.value),
                                        "source_url": f.source_url})
        out.append({"id": pc.id, "name": _t(pc.name), "sector": _t(pc.sector) if pc.sector else None,
                    "first_invested_year": pc.first_invested_year, "stage_at_entry": _t(pc.stage_at_entry) if pc.stage_at_entry else None,
                    "source_url": pc.source_url, "facts": series})
    latest = max((f["as_of"] for c in out for s in c["facts"].values() for f in s), default=None)
    return {"label": "Public data only. Not Anti Fund's actual marks or metrics.", "as_of": latest, "companies": out}


BACKTEST_DIR = REPO_ROOT / "eval" / "backtest"


def _backtest(bt_dir: Path = BACKTEST_DIR) -> dict[str, Any]:
    """Protocol constants are read from the backtest script itself so they cannot drift. Result
    numbers are copied from results.json and formd_results.json; nothing is computed here."""
    src = bt_dir / "run_backtest.py"
    consts: dict[str, Any] = {}
    if src.exists():
        text = src.read_text()
        for name in ("OFFSETS", "ENRICH_TOP", "SCORE_TOP", "LOOKBACK_DAYS"):
            m = re.search(rf"^{name} = (.+)$", text, re.M)
            if m:
                consts[name.lower()] = m.group(1).split("#")[0].strip()
    res_p, fd_p = bt_dir / "results.json", bt_dir / "formd_results.json"
    if not (res_p.exists() and fd_p.exists()):
        return {"status": "pending", "message": "Results pending: corrective rerun in progress.", "protocol": consts}
    res, fd = json.loads(res_p.read_text()), json.loads(fd_p.read_text())
    formd = {}
    for h, r in fd["horizons"].items():
        a, std = r["all"], r["type_standardized"]
        formd[h] = {"cutoffs": len(r["eligible_cutoffs"]),
                    "rates": {arm: {"hits": a[arm]["hits"], "n": a[arm]["observations"], "rate": a[arm]["rate"]}
                              for arm in ("radar", "stars", "random")},
                    "raw_lift": a["lift_radar_vs_random"],
                    "standardized": std["radar"]}
    rows = [{"company": _t(r["company"]), "set": r["set"], "announced_on": r["announced_on"],
             "included": r["included"], "offset_days": r["offset_days"], "ever_collected": r["ever_collected"],
             "in_universe": r["in_universe"], "rank": r["rank"]} for r in res["rows"]]
    headline = res["headline"]
    return {"status": "results", "message": "Corrective run (labels v2, resolver v2).",
            "fingerprint": res.get("code_fingerprint"), "generated": res.get("generated"), "protocol": consts,
            "formd": {"data_end": fd["data_end"], "top": fd["top"], "horizons": formd},
            "portfolio": {"n_primary": headline["n_companies"], "n_secondary": res["secondary"]["n_companies"],
                          "found_top100": headline["companies_found_top100"] + res["secondary"]["companies_found_top100"],
                          "ever_collected": sum(1 for r in rows if r["ever_collected"]) // 2,
                          "rows": rows},
            "query_audit": {"passed": res["query_audit"]["passed"],
                            "distinct_queries": res["query_audit"]["distinct_queries"]}}


def export(session: Session, out_dir: Path = OUT_DIR, *, extra_agent_sessions: list[tuple[str, Session]] | None = None,
           ledger: Path | None = None, top: int = 600, backtest_dir: Path = BACKTEST_DIR) -> dict[str, int]:
    as_of = session.scalar(select(func.max(Score.as_of)))
    if out_dir.exists():
        shutil.rmtree(out_dir / "companies", ignore_errors=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    ranked: list[tuple[Score, Company]] = []
    if as_of is not None:
        ranked = [(sc, co) for sc, co in session.execute(select(Score, Company).join(Company, Company.id == Score.company_id)
                                                         .where(Score.as_of == as_of).order_by(Score.rank).limit(top))]
    why = _why_now(session, ranked)
    memo_ids = set(session.scalars(select(Memo.company_id)))
    cfg = weights()["groups"]
    rows = []
    for s, c in ranked:
        ex = explain(_scored(session, s), top_k=3)
        groups = (s.breakdown_json or {}).get("groups", {})
        rows.append({
            "id": c.id, "rank": s.rank, "name": _t(c.name), "sector": c.sector, "kind": c.kind,
            "domain": c.domain, "github_org": c.github_org, "total": round(s.total, 4),
            "groups": {g: round(float(groups.get(g, 0.0)), 4) for g in cfg},
            "top_evidence": [{"feature": e["feature"], "label": _t(e.get("label", "")), "url": e.get("url"),
                              "at": e.get("at")} for e in ex.top_evidence],
            "why_now": why.get(c.id, {}).get("text"), "has_memo": c.id in memo_ids,
        })
    _write(out_dir / "radar.json", {
        "as_of": _iso(as_of), "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "groups": [{"key": g, "label": GROUP_LABELS.get(g, g), "weight": spec["weight"]} for g, spec in cfg.items()],
        "sectors": sorted({r["sector"] for r in rows if r["sector"]}), "rows": rows,
    })
    for s, c in ranked:
        _write(out_dir / "companies" / f"{c.id}.json", _company(session, s, c, why.get(c.id)))
    items = [(pi, co) for pi, co in session.execute(select(PipelineItem, Company).join(Company, Company.id == PipelineItem.company_id))]
    _write(out_dir / "pipeline.json", {"stages": PIPELINE_STAGES, "items": [
        {"company_id": c.id, "name": _t(c.name), "stage": p.stage, "owner": p.owner, "updated_at": _iso(p.updated_at),
         "notes": _t(p.notes) if p.notes else None} for p, c in items]})
    _write(out_dir / "agent_ops.json", _agent_ops([("live", session), *(extra_agent_sessions or [])],
                                                  ledger or REPO_ROOT / "data" / "cost_ledger.jsonl"))
    memos = list(session.scalars(select(Memo).order_by(Memo.created_at)))
    ops = json.loads((out_dir / "agent_ops.json").read_text())
    ops["verifier_trend"] = [{"memo_id": m.id, "company_id": m.company_id, "created_at": _iso(m.created_at),
                              "supported_rate": (m.verifier_summary_json or {}).get("supported_rate"),
                              "checked": (m.verifier_summary_json or {}).get("checked")} for m in memos]
    _write(out_dir / "agent_ops.json", ops)
    _write(out_dir / "portfolio.json", _portfolio(session))
    _write(out_dir / "backtest.json", _backtest(backtest_dir))
    queue = list(session.scalars(select(EntityCandidate).where(EntityCandidate.status == "pending").order_by(EntityCandidate.score.desc())))
    _write(out_dir / "review_queue.json", {"items": [
        {"id": q.id, "a_key": q.a_key, "b_key": q.b_key, "a_label": _t(q.a_label), "b_label": _t(q.b_label),
         "score": q.score, "rationale": _t(q.rationale) if q.rationale else None, "created_at": _iso(q.created_at)}
        for q in queue]})
    return {"companies": len(ranked), "memos": len(memo_ids), "agent_runs": len(ops["runs"]), "queue": len(queue)}
