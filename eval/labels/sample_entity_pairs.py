"""Draw the 100 entity-resolution pairs for the precision gate (brief Part 5.12).

Re-runs the resolver's own comparison (tiers 1 and 2, no LLM judge, as in the backtest) over
data/backtest.db and samples a fixed-seed, stratified set of pairs:

- 60 pairs the resolver merged (split across deterministic domain, GitHub login, distinctive
  name, and fuzzy merges in proportion to how often each happened, at least 5 each when
  available), because the gate is precision over merges;
- 20 pairs it queued as ambiguous;
- 20 pairs it kept separate.

When the resolver made fewer than 100 decisions in total (the backtest universe has few
cross-source pairs), every decision is taken (a census, not a sample) and the remainder is
filled with blocked pairs it compared and declined (name similarity below the fuzzy
threshold), recorded as resolver_decision "below_threshold".

Each row carries the context a labeler needs (Hacker News title and URL, repository owner and
homepage, award recipient). The `label` column (same | different | unsure) is filled by hand
under the rubric in eval/labels/README.md; this script never writes labels.

Run: uv run python eval/labels/sample_entity_pairs.py
Writes eval/labels/entity_pairs.csv (refuses to overwrite a file that already has labels).
"""

from __future__ import annotations

import csv
import os
import random
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("DATABASE_URL", f"sqlite:///{ROOT / 'data' / 'backtest.db'}")
sys.path.insert(0, str(ROOT))


from radar.collectors.common import clean_text  # noqa: E402
from radar.entities.resolve import Decision, Mention, candidate_pairs, collect_mentions, compare  # noqa: E402
from radar.store.db import session_scope  # noqa: E402
from radar.store.models import Award, HNPost, Repo  # noqa: E402

OUT = Path(__file__).resolve().parent / "entity_pairs.csv"
SEED = 20261102
N_MERGE, N_AMBIGUOUS, N_SEPARATE, MIN_PER_STRATUM = 60, 20, 20, 5
FIELDS = ["pair_id", "resolver_decision", "method", "stratum", "score", "rationale",
          "a_key", "a_label", "a_context", "b_key", "b_label", "b_context",
          "label", "labeled_by", "notes"]


def stratum(d: Decision) -> str:
    if d.verdict != "merge":
        return d.verdict
    if d.rationale.startswith("same domain"):
        return "merge:domain"
    if d.rationale.startswith("same GitHub login"):
        return "merge:login"
    if d.rationale.startswith("same distinctive name"):
        return "merge:name"
    return "merge:fuzzy"


def context(session, m: Mention) -> str:  # type: ignore[no-untyped-def]
    table, row_id, _ = m.rows[0]
    if table == "hn_posts":
        p = session.get(HNPost, row_id)
        return f"HN {p.created_at:%Y-%m-%d}: {p.title} | {p.url or ''}"
    if table == "repos":
        repos = [session.get(Repo, r) for t, r, _ in m.rows[:3] if t == "repos"]
        return " ; ".join(f"{r.full_name} ({r.homepage or 'no homepage'}): {(r.description or '')[:80]}" for r in repos)
    if table == "awards":
        a = session.get(Award, row_id)
        return f"award {a.award_date}: {a.recipient} | {(a.topic or '')[:100]} ({a.agency or ''})"
    return table


def allocate(counts: dict[str, int], total: int) -> dict[str, int]:
    """Proportional allocation with a floor, capped by availability."""
    n = sum(counts.values()) or 1
    alloc = {k: min(v, max(MIN_PER_STRATUM, round(total * v / n))) for k, v in counts.items()}
    while sum(alloc.values()) > total:
        k = max(alloc, key=lambda k: alloc[k])
        alloc[k] -= 1
    spare = total - sum(alloc.values())
    for k in sorted(counts, key=lambda k: -counts[k]):
        add = min(spare, counts[k] - alloc[k])
        alloc[k] += add
        spare -= add
    return alloc


def main() -> None:
    if OUT.exists():
        with open(OUT, newline="") as f:
            if any(r.get("label") for r in csv.DictReader(f)):
                sys.exit(f"{OUT} already has labels; move it aside to resample")
    rng = random.Random(SEED)
    with session_scope() as s:
        mentions = collect_mentions(s)
        by_stratum: dict[str, list[Decision]] = defaultdict(list)
        declined: list[tuple[Mention, Mention]] = []
        for a, b in candidate_pairs(mentions):
            d = compare(a, b)
            if d is not None:
                by_stratum[stratum(d)].append(d)
            else:
                declined.append((a, b))
        counts = {k: len(v) for k, v in by_stratum.items()}
        print("decisions by stratum:", dict(sorted(counts.items())))
        total = N_MERGE + N_AMBIGUOUS + N_SEPARATE
        if sum(counts.values()) <= total:
            plan = dict(counts)  # census
            fill = rng.sample(sorted(declined, key=lambda p: (p[0].key, p[1].key)),
                              min(len(declined), total - sum(counts.values())))
            by_stratum["below_threshold"] = [Decision(a, b, 0.0, "below_threshold", "blocking", 0.0,
                                                      "blocked together, name similarity below threshold")
                                             for a, b in fill]
            plan["below_threshold"] = len(fill)
        else:
            merge_alloc = allocate({k: v for k, v in counts.items() if k.startswith("merge")}, N_MERGE)
            plan = {**merge_alloc, "ambiguous": min(N_AMBIGUOUS, counts.get("ambiguous", 0)),
                    "separate": min(N_SEPARATE, counts.get("separate", 0))}
        rows = []
        for k, n in plan.items():
            pool = sorted(by_stratum[k], key=lambda d: (d.a.key, d.b.key))
            for d in rng.sample(pool, n):
                rows.append({"resolver_decision": d.verdict, "method": d.method, "stratum": k,
                             "score": round(d.score, 1), "rationale": d.rationale,
                             "a_key": d.a.key, "a_label": clean_text(d.a.label), "a_context": clean_text(context(s, d.a)),
                             "b_key": d.b.key, "b_label": clean_text(d.b.label), "b_context": clean_text(context(s, d.b)),
                             "label": "", "labeled_by": "", "notes": ""})
    rng.shuffle(rows)
    for i, r in enumerate(rows, 1):
        r["pair_id"] = f"P{i:03d}"
    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(rows)} pairs to {OUT}; plan {plan}")


if __name__ == "__main__":
    main()
