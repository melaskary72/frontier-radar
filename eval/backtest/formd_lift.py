"""Form D outcome backtest: do the radar's top 100 go on to raise more often than chance?

For every backtest cutoff whose outcome window has fully elapsed, three arms drawn from the
same point-in-time in-thesis universe:
- radar: the radar's top 100 by score;
- stars: the top 100 by GitHub stars (hindsight-advantaged: today's counts, BUILD_CONTRACT D5);
- random: the in-thesis universe as a whole (the expected value of a random 100), plus one
  seeded random draw of 100 per cutoff reported for transparency.

Outcome: a new Form D (not an amendment) filed after the cutoff and within 6 or 12 months.
Matching (identical for every arm): exact normalized name of the company or its GitHub
organization against the Form D issuer name, distinctive names only (two or more words or at
least eight characters), so "Atlas" never matches "Atlas Fund".

Rules fixed before results: complete windows only; pooled over cutoffs; 95% intervals from a
bootstrap that resamples companies (a company appears at many cutoffs, so cutoffs are not
independent); lift = radar rate / random rate. Sensitivities: (a) excluding companies with any
Form D on or before the cutoff, so "already funded companies raise again" cannot drive the lift;
(b) only companies whose names are matchable at all.

Inputs: data/backtest_cutoffs/*.json (fingerprint from eval/backtest/results.json),
data/backtest.db, data/cache/formd/filings.csv (eval/backtest/formd_fetch.py).
Writes eval/backtest/formd_results.json and eval/backtest/formd_matches.csv.
Run: uv run python eval/backtest/formd_lift.py
"""

from __future__ import annotations

import csv
import json
import os
import random
import sys
from bisect import bisect_right
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("DATABASE_URL", f"sqlite:///{ROOT / 'data' / 'backtest.db'}")
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
from sqlalchemy import select  # noqa: E402

from radar.entities.resolve import distinctive, strict_name  # noqa: E402
from radar.store.db import session_scope  # noqa: E402
from radar.store.models import Company  # noqa: E402

HERE = Path(__file__).resolve().parent
CUTOFFS = ROOT / "data" / "backtest_cutoffs"
FILINGS = ROOT / "data" / "cache" / "formd" / "filings.csv"
HORIZONS = {"6m": 182, "12m": 365}
TOP = 100
B = 5000
SEED = 20261103


def load_filings() -> tuple[dict[str, list[date]], dict[str, list[date]], date, dict[str, str]]:
    new: dict[str, list[date]] = defaultdict(list)
    anyf: dict[str, list[date]] = defaultdict(list)
    names: dict[str, str] = {}
    latest = date.min
    with open(FILINGS, newline="") as f:
        for r in csv.DictReader(f):
            key = strict_name(r["entity_name"])
            if not key or not distinctive(r["entity_name"]):
                continue
            d = date.fromisoformat(r["filed_on"])
            latest = max(latest, d)
            anyf[key].append(d)
            names.setdefault(key, r["entity_name"])
            if r["is_amendment"] == "0":
                new[key].append(d)
    for m in (new, anyf):
        for v in m.values():
            v.sort()
    return new, anyf, latest, names


def company_keys(c: Company) -> list[str]:
    keys = []
    for s in (c.name, (c.github_org or "").replace("-", " ").replace("_", " ")):
        if s and distinctive(s) and strict_name(s):
            keys.append(strict_name(s))
    return list(dict.fromkeys(keys))


def wilson(k: int, n: int) -> list[float]:
    if n == 0:
        return [0.0, 0.0]
    z, p = 1.96, k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / den
    return [round(max(0.0, c - h), 4), round(min(1.0, c + h), 4)]


def cluster_boot(obs: list[tuple[int, int]], rng: np.random.Generator) -> np.ndarray:
    """obs: (company_id, hit). Returns B bootstrap rates resampling companies."""
    by: dict[int, list[int]] = defaultdict(list)
    for cid, hit in obs:
        by[cid].append(hit)
    ids = list(by)
    hits = np.array([sum(by[i]) for i in ids], dtype=float)
    ns = np.array([len(by[i]) for i in ids], dtype=float)
    idx = rng.integers(0, len(ids), size=(B, len(ids)))
    return hits[idx].sum(axis=1) / ns[idx].sum(axis=1)


def main() -> None:
    fp = json.loads((HERE / "results.json").read_text())["code_fingerprint"]
    cutoffs = {}
    for p in sorted(CUTOFFS.glob("*.json")):
        d = json.loads(p.read_text())
        if d.get("fingerprint") == fp:
            cutoffs[date.fromisoformat(p.stem)] = d["result"]
    new, anyf, latest, issuer_names = load_filings()
    data_end = latest - timedelta(days=1)  # the newest index day may be partial
    with session_scope() as s:
        comp = {c.id: c for c in s.scalars(select(Company))}
    keys = {cid: company_keys(c) for cid, c in comp.items()}

    def kind(cid: int) -> str:
        return comp[cid].kind if cid in comp else "unknown"

    def filed(cid: int, start: date, end: date, table: dict[str, list[date]]) -> bool:
        for k in keys.get(cid, []):
            v = table.get(k, [])
            if bisect_right(v, end) - bisect_right(v, start) > 0:
                return True
        return False

    def prior(cid: int, cutoff: date) -> bool:
        return any(bisect_right(anyf.get(k, []), cutoff) > 0 for k in keys.get(cid, []))

    rng_draw = random.Random(SEED)
    out: dict[str, Any] = {"fingerprint": fp, "data_end": data_end.isoformat(), "top": TOP,
                           "matching": "exact normalized name or GitHub org vs Form D issuer, distinctive names only",
                           "horizons": {}}
    matches: list[dict[str, str]] = []
    for hname, days in HORIZONS.items():
        eligible = sorted(c for c in cutoffs if c + timedelta(days=days) <= data_end)
        variants: dict[str, dict[str, list[tuple[int, int]]]] = {v: defaultdict(list) for v in
                                                                 ("all", "no_prior_form_d", "matchable_only")}
        per_cutoff = []
        # Type-standardized control, added after seeing that the radar's top 100 is mostly
        # organizations and awardees while the in-thesis universe is 92% individual accounts:
        # each radar or stars entry is expected to hit at the in-thesis rate of its own kind
        # (org, awardee, user, launch) at that cutoff. contrib[arm][company] = [observed, expected].
        contrib: dict[str, dict[int, list[float]]] = {"radar": defaultdict(lambda: [0.0, 0.0]),
                                                      "stars": defaultdict(lambda: [0.0, 0.0])}
        for c in eligible:
            r = cutoffs[c]
            ranks = {int(k): v for k, v in r["ranks"].items()}
            base = {int(k): v for k, v in r["baseline_ranks"].items()}
            arms = {"radar": [i for i, v in ranks.items() if v <= TOP],
                    "stars": [i for i, v in base.items() if v <= TOP],
                    "random": list(r["in_thesis"])}
            draw = rng_draw.sample(arms["random"], min(TOP, len(arms["random"])))
            end = c + timedelta(days=days)
            row: dict[str, Any] = {"cutoff": c.isoformat()}
            all_hits: dict[str, dict[int, int]] = {}
            for arm, ids in [*arms.items(), ("random_draw_100", draw)]:
                hits = {i: int(filed(i, c, end, new)) for i in ids}
                all_hits[arm] = hits
                row[arm] = {"n": len(ids), "hits": sum(hits.values())}
                if arm == "random_draw_100":
                    continue
                for i, h in hits.items():
                    variants["all"][arm].append((i, h))
                    if not prior(i, c):
                        variants["no_prior_form_d"][arm].append((i, h))
                    if keys.get(i):
                        variants["matchable_only"][arm].append((i, h))
                    if h and arm != "random":
                        matches.append({"horizon": hname, "cutoff": c.isoformat(), "arm": arm, "company_id": str(i),
                                        "company": comp[i].name if i in comp else "", "github_org": (comp[i].github_org or "") if i in comp else "",
                                        "issuer": "; ".join(issuer_names[k] for k in keys[i] if k in new)})
            kind_n: dict[str, int] = defaultdict(int)
            kind_k: dict[str, int] = defaultdict(int)
            for i, h in all_hits["random"].items():
                kind_n[kind(i)] += 1
                kind_k[kind(i)] += h
            row["in_thesis_rate_by_kind"] = {k: round(kind_k[k] / kind_n[k], 6) for k in kind_n}
            for arm in ("radar", "stars"):
                for i, h in all_hits[arm].items():
                    k = kind(i)
                    contrib[arm][i][0] += h
                    contrib[arm][i][1] += kind_k[k] / kind_n[k] if kind_n[k] else 0.0
            per_cutoff.append(row)
        res: dict[str, Any] = {"eligible_cutoffs": [c.isoformat() for c in eligible], "per_cutoff": per_cutoff}
        std_rng = np.random.default_rng(SEED + 1)
        res["type_standardized"] = {}
        for arm, cc in contrib.items():
            ids = list(cc)
            o = np.array([cc[i][0] for i in ids])
            e = np.array([cc[i][1] for i in ids])
            idx = std_rng.integers(0, len(ids), size=(B, len(ids)))
            ratio = o[idx].sum(axis=1) / e[idx].sum(axis=1)
            res["type_standardized"][arm] = {
                "observed_hits": int(o.sum()), "expected_hits_at_in_thesis_rate_of_same_kind": round(float(e.sum()), 3),
                "companies_with_a_hit": int((o > 0).sum()), "companies": len(ids),
                "lift": round(float(o.sum() / e.sum()), 3) if e.sum() else None,
                "ci95": [round(float(np.percentile(ratio, q)), 3) for q in (2.5, 97.5)],
                "p_lift_le_1": round(float((ratio <= 1).mean()), 4)}
        rng = np.random.default_rng(SEED)
        for v, arms_obs in variants.items():
            block: dict[str, Any] = {}
            boots = {}
            for arm, obs in arms_obs.items():
                k, n = sum(h for _, h in obs), len(obs)
                boots[arm] = cluster_boot(obs, rng) if obs else np.zeros(B)
                block[arm] = {"observations": n, "companies": len({i for i, _ in obs}), "hits": k,
                              "rate": round(k / n, 5) if n else None, "rate_ci95_wilson": wilson(k, n),
                              "rate_ci95_cluster_bootstrap": [round(float(np.percentile(boots[arm], q)), 5) for q in (2.5, 97.5)]}
            for num, den in (("radar", "random"), ("radar", "stars"), ("stars", "random")):
                a, b_ = block[num]["rate"], block[den]["rate"]
                ratio = boots[num] / np.where(boots[den] > 0, boots[den], np.nan)
                ratio = ratio[np.isfinite(ratio)]
                block[f"lift_{num}_vs_{den}"] = {
                    "lift": round(a / b_, 3) if a is not None and b_ else None,
                    "ci95": [round(float(np.percentile(ratio, q)), 3) for q in (2.5, 97.5)] if len(ratio) > B // 2 else None,
                    "bootstrap_draws_used": len(ratio),
                    "p_lift_le_1": round(float((ratio <= 1).mean()), 4) if len(ratio) else None}
            res[v] = block
        out["horizons"][hname] = res
        a = res["all"]
        print(f"{hname}: {len(eligible)} cutoffs | radar {a['radar']['hits']}/{a['radar']['observations']} "
              f"stars {a['stars']['hits']}/{a['stars']['observations']} random {a['random']['hits']}/{a['random']['observations']} | "
              f"lift vs random {a['lift_radar_vs_random']} | vs stars {a['lift_radar_vs_stars']}")
        print(f"   type-standardized radar: {res['type_standardized']['radar']}")
    (HERE / "formd_results.json").write_text(json.dumps(out, indent=1))
    with open(HERE / "formd_matches.csv", "w", newline="") as f:
        fields = ["horizon", "cutoff", "arm", "company_id", "company", "github_org", "issuer"]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(matches)


if __name__ == "__main__":
    main()
