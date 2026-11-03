"""Recompute the resolver's decision for every labeled entity pair with the current code.

Labels never change here. The v1 decision is preserved in `resolver_decision_v1` the first
time this runs; `resolver_decision` always holds the current resolver's verdict, which is what
eval/gates.py scores. Pairs are matched by mention key, so the same 100 pairs are compared.

Run: uv run python eval/labels/rescore_entity_pairs.py
"""

from __future__ import annotations

import csv
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("DATABASE_URL", f"sqlite:///{ROOT / 'data' / 'backtest.db'}")
sys.path.insert(0, str(ROOT))

from radar.entities.resolve import collect_mentions, compare  # noqa: E402
from radar.store.db import session_scope  # noqa: E402

PAIRS = Path(__file__).resolve().parent / "entity_pairs.csv"


def main() -> None:
    with open(PAIRS, newline="") as f:
        rows = list(csv.DictReader(f))
    fields = list(rows[0].keys())
    if "resolver_decision_v1" not in fields:
        fields.insert(fields.index("resolver_decision") + 1, "resolver_decision_v1")
        for r in rows:
            r["resolver_decision_v1"] = r["resolver_decision"]
    with session_scope() as s:
        by_key = {m.key: m for m in collect_mentions(s)}
    missing = 0
    for r in rows:
        a, b = by_key.get(r["a_key"]), by_key.get(r["b_key"])
        if a is None or b is None:
            missing += 1
            continue
        d = compare(a, b)
        r["resolver_decision"] = "below_threshold" if d is None else d.verdict
    with open(PAIRS, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    moved = Counter((r["resolver_decision_v1"], r["resolver_decision"], r["label"]) for r in rows
                    if r["resolver_decision_v1"] != r["resolver_decision"])
    print(f"{len(rows)} pairs, {missing} with a missing mention; changed decisions: {dict(moved)}")


if __name__ == "__main__":
    main()
