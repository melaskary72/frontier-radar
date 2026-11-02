"""Scoring: percentile-normalize features within (sector, as_of), weight, rank.

Rules
- A feature's percentile is 0 when its raw value is 0 or missing (no evidence, no credit),
  otherwise its percentile rank among companies in the same sector at the same date.
- A feature that is unavailable for everyone (for example hiring in a backtest) is dropped
  from its group; a group with no available features is dropped and the remaining group
  weights are renormalized, so totals stay on a 0 to 1 scale.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Any

import pandas as pd
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from radar.config import weights
from radar.scoring import features as F
from radar.scoring.thesis import LLMJudge, company_text, thesis_probability
from radar.store.models import Company, Feature, Score


@dataclass
class Scored:
    company_id: int
    total: float
    rank: int
    groups: dict[str, float]           # weighted contribution per group
    percentiles: dict[str, float]      # per feature
    raw: dict[str, float | None]
    evidence: dict[str, list[dict[str, Any]]]


def percentile_frame(raw: pd.DataFrame, sectors: pd.Series) -> pd.DataFrame:
    """raw: index company_id, columns feature names. Returns 0..1 percentiles."""
    out = pd.DataFrame(0.0, index=raw.index, columns=raw.columns)
    for _, idx in sectors.fillna("unassigned").groupby(sectors.fillna("unassigned")).groups.items():
        block = raw.loc[idx]
        for col in raw.columns:
            vals = block[col]
            pos = vals[vals.fillna(0) > 0]
            if len(pos):
                # rank among all companies in the sector; zeros and missing stay at 0
                ranks = vals.fillna(0).rank(method="max", pct=True)
                out.loc[pos.index, col] = ranks.loc[pos.index]
    return out


def group_scores(pct: pd.DataFrame, available: set[str]) -> tuple[pd.DataFrame, dict[str, float]]:
    cfg = weights()["groups"]
    groups, w = {}, {}
    for g, spec in cfg.items():
        cols = [f for f in spec["features"] if f in available and f in pct.columns]
        if cols:
            groups[g] = pct[cols].mean(axis=1)
            w[g] = float(spec["weight"])
    norm = sum(w.values()) or 1.0
    return pd.DataFrame(groups), {g: v / norm for g, v in w.items()}


def score_companies(session: Session, companies: Iterable[Company], as_of: date, *, pit_only: bool,
                    judge: LLMJudge | None = None, persist: bool = True,
                    thesis: dict[int, tuple[float, str]] | None = None) -> list[Scored]:
    companies = list(companies)
    if not companies:
        return []
    raw: dict[int, dict[str, float | None]] = {}
    evidence: dict[int, dict[str, list[dict[str, Any]]]] = {}
    for c in companies:
        data = F.load_company_data(session, c, as_of)
        vals = F.compute(data, pit_only=pit_only)
        raw[c.id] = {k: v.value for k, v in vals.items()}
        evidence[c.id] = {k: [e.as_dict() for e in v.evidence] for k, v in vals.items() if v.evidence}

    if thesis is None:
        texts = [company_text(session, c) for c in companies]
        thesis = {c.id: pm for c, pm in zip(companies, thesis_probability(texts, judge), strict=True)}
    for c in companies:
        p, method = thesis[c.id]
        raw[c.id]["thesis_probability"] = p
        evidence[c.id]["thesis_probability"] = [{"snapshot_id": None, "label": f"thesis fit {p:.2f} ({method})",
                                                 "url": None, "at": as_of.isoformat()}]

    frame = pd.DataFrame.from_dict(raw, orient="index")
    available = {c for c in frame.columns if frame[c].notna().any()}
    sectors = pd.Series({c.id: c.sector for c in companies})
    pct = percentile_frame(frame[sorted(available)], sectors)
    groups, gw = group_scores(pct, available)
    contrib = groups.mul(pd.Series(gw), axis=1)
    total = contrib.sum(axis=1).sort_values(ascending=False)

    results = []
    for rank, (cid, tot) in enumerate(total.items(), start=1):
        results.append(Scored(
            company_id=int(cid), total=float(tot), rank=rank,
            groups={g: float(contrib.at[cid, g]) for g in contrib.columns},
            percentiles={f: float(pct.at[cid, f]) for f in pct.columns},
            raw={k: (None if pd.isna(v) else float(v)) for k, v in frame.loc[cid].items()},
            evidence=evidence[int(cid)],
        ))
    if persist:
        _persist(session, results, as_of, gw)
    return results


def _persist(session: Session, results: list[Scored], as_of: date, gw: dict[str, float]) -> None:
    ids = [r.company_id for r in results]
    session.execute(delete(Feature).where(Feature.as_of == as_of, Feature.company_id.in_(ids)))
    session.execute(delete(Score).where(Score.as_of == as_of, Score.company_id.in_(ids)))
    for r in results:
        for name, value in r.raw.items():
            ev = r.evidence.get(name, [])
            session.add(Feature(company_id=r.company_id, as_of=as_of, name=name, value=value,
                                evidence_snapshot_ids_json=[e["snapshot_id"] for e in ev if e["snapshot_id"]],
                                evidence_json=ev))
        session.add(Score(company_id=r.company_id, as_of=as_of, total=r.total, rank=r.rank,
                          breakdown_json={"groups": r.groups, "group_weights": gw,
                                          "percentiles": r.percentiles}))
    session.flush()


def latest_scores(session: Session, as_of: date) -> list[Score]:
    return list(session.scalars(select(Score).where(Score.as_of == as_of).order_by(Score.rank)))
