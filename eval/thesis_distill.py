"""Distillation experiment: train the thesis classifier on judge-labeled live texts, test on
the 117 human labels (v2) that are never used for training.

Why: with 117 labels, nested CV shows no feature set beats the baseline (thesis_experiments.py),
so labels, not features, are the bottleneck. The Haiku thesis judge labels a larger sample of
live-radar company texts offline; the classifier learns from those; the backtest still runs the
classifier only, with no LLM at inference (see BUILD_CONTRACT: anti-hindsight design).

Guards:
- Training texts come from data/live.db company descriptions, deduplicated.
- Texts matching any portfolio name or domain in eval/leakage_terms.yaml are dropped, so the
  judge never labels a company it might know became famous.
- Texts identical to any of the 117 test items are dropped.
- Spend is checked against the $15 cap before every call (radar.agents.cost) and logged.

Run: uv run python eval/thesis_distill.py [--n 1000]
Writes eval/labels/thesis_distill_judge.csv and eval/thesis_distill.json.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import yaml  # noqa: E402
from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.model_selection import StratifiedKFold, cross_val_predict  # noqa: E402
from sqlalchemy import create_engine, select  # noqa: E402

from radar.scoring.thesis import company_text, default_embedder, load_labels  # noqa: E402
from radar.store.db import create_all, session_scope  # noqa: E402
from radar.store.models import Company  # noqa: E402

sys.path.insert(0, str(ROOT / "eval"))
from thesis_experiments import C_GRID, CANDIDATES, build_blocks  # noqa: E402

LABELS = ROOT / "eval" / "labels"
JUDGED = LABELS / "thesis_distill_judge.csv"
OUT = ROOT / "eval" / "thesis_distill.json"
SEED = 20261102


def leakage_patterns() -> list[re.Pattern[str]]:
    terms = yaml.safe_load((ROOT / "eval" / "leakage_terms.yaml").read_text())["companies"]
    pats = []
    for t in terms:
        pats.append(re.compile(rf"(?<![\w-]){re.escape(t['name'])}(?![\w-])", re.I))
        for d in t.get("domains") or []:
            pats.append(re.compile(re.escape(d), re.I))
    return pats


def sample_texts(n: int, test_texts: set[str]) -> list[str]:
    live = create_engine(f"sqlite:///{ROOT / 'data' / 'live.db'}")
    pats = leakage_patterns()
    with session_scope(live) as s:
        texts = {company_text(s, c) for c in s.scalars(select(Company).where(Company.kind != "stale"))}
    clean = sorted(t for t in texts if t and t not in test_texts and not any(p.search(t) for p in pats))
    print(f"{len(texts)} live texts, {len(clean)} after leakage and test-set exclusion")
    return random.Random(SEED).sample(clean, min(n, len(clean)))


def judge_all(texts: list[str]) -> list[dict[str, str]]:
    from radar.agents.judges import ThesisJudge

    done: dict[str, dict[str, str]] = {}
    if JUDGED.exists():
        with open(JUDGED, newline="") as f:
            done = {r["text"]: r for r in csv.DictReader(f)}
    ops = create_engine(f"sqlite:///{ROOT / 'data' / 'radar.db'}")  # agent_runs log lives here
    create_all(ops)
    rows = [done[t] for t in texts if t in done]
    todo = [t for t in texts if t not in done]
    with session_scope(ops) as s:
        agent = ThesisJudge(s)
        for i, t in enumerate(todo, 1):
            r = agent.run(t)
            rows.append({"text": t, "judge_probability": str(r.output.probability), "status": r.status,
                         "rationale": r.output.rationale})
            if i % 50 == 0:
                s.commit()
                _write(rows)
                print(f"judged {len(rows)} of {len(texts)}")
    _write(rows)
    return rows


def _write(rows: list[dict[str, str]]) -> None:
    with open(JUDGED, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["text", "judge_probability", "status", "rationale"])
        w.writeheader()
        w.writerows(rows)


def clf(c: float) -> LogisticRegression:
    return LogisticRegression(C=c, class_weight="balanced", max_iter=5000)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=1000)
    args = ap.parse_args()
    test_texts, test_y = load_labels(LABELS / "thesis_labels.csv")
    yt = np.array(test_y)
    train = [r for r in judge_all(sample_texts(args.n, set(test_texts))) if r["status"] == "ok"]
    tr_texts = [r["text"] for r in train]
    ytr = np.array([1 if float(r["judge_probability"]) >= 0.5 else 0 for r in train])
    print(f"training on {len(train)} judge labels ({ytr.sum()} positive); testing on {len(yt)} human labels")
    btr, bte = build_blocks(tr_texts, default_embedder), build_blocks(test_texts, default_embedder)
    results = {}
    for cand, parts in CANDIDATES.items():
        Xtr = np.hstack([btr[b] for b in parts])
        Xte = np.hstack([bte[b] for b in parts])
        # C chosen by CV on the training (judge-labeled) set only; the test set is touched once.
        by_c = {c: float((cross_val_predict(clf(c), Xtr, ytr, cv=StratifiedKFold(5, shuffle=True, random_state=7))
                         == ytr).mean()) for c in C_GRID}
        best_c = max(by_c, key=lambda c: by_c[c])
        pred = clf(best_c).fit(Xtr, ytr).predict(Xte)
        results[cand] = {"C": best_c, "train_cv_vs_judge": round(by_c[best_c], 3),
                         "test_accuracy_vs_human": round(float((pred == yt).mean()), 3)}
        print(f"  {cand:20} C={best_c}: train CV vs judge {by_c[best_c]:.3f}, test vs human {results[cand]['test_accuracy_vs_human']:.3f}")
    OUT.write_text(json.dumps({"generated": date.today().isoformat(), "n_train": len(train),
                               "train_positives": int(ytr.sum()), "n_test": len(yt),
                               "test_labels": "eval/labels/thesis_labels.csv (v2)", "results": results}, indent=1))


if __name__ == "__main__":
    main()
