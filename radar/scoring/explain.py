"""Explain a score: per-group contributions and the top three evidence items with links."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from radar.config import weights
from radar.scoring.score import Scored


@dataclass
class Explanation:
    company_id: int
    total: float
    rank: int
    contributions: list[dict[str, Any]]  # [{group, contribution, share}], largest first
    top_evidence: list[dict[str, Any]]   # [{feature, label, url, snapshot_id, at, weight}]


def explain(s: Scored, top_k: int = 3) -> Explanation:
    cfg = weights()["groups"]
    contribs: list[dict[str, Any]] = sorted(
        ({"group": g, "contribution": round(v, 4), "share": round(v / s.total, 3) if s.total else 0.0}
         for g, v in s.groups.items()),
        key=lambda x: -float(x["contribution"]))

    # Rank features by how much they moved the total: group contribution split evenly
    # across that group's available features, times the feature's percentile.
    feature_weight: dict[str, float] = {}
    for g, spec in cfg.items():
        feats = [f for f in spec["features"] if f in s.percentiles]
        if not feats or g not in s.groups:
            continue
        group_pct = sum(s.percentiles[f] for f in feats) or 1.0
        for f in feats:
            feature_weight[f] = s.groups[g] * s.percentiles[f] / group_pct

    items = []
    for f, w in sorted(feature_weight.items(), key=lambda kv: -kv[1]):
        if w <= 0:
            continue
        for ev in s.evidence.get(f, [])[:1]:
            items.append({"feature": f, "weight": round(w, 4), **ev})
        if len(items) >= top_k:
            break
    return Explanation(s.company_id, s.total, s.rank, contribs, items)
