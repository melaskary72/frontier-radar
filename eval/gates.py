"""Eval gates (brief Part 5.12), computed from logged runs and labeled files.

Each gate reports value, target, status (pass | fail | pending), and method. Failed gates stay
failed in the output; the README reads eval/gates.json and shows them with their cause.

Run: uv run python eval/gates.py [--thesis-judge]  (the flag spends about $0.03 on Haiku)
"""

from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
from sklearn.model_selection import StratifiedKFold, cross_val_predict  # noqa: E402

from radar.agents import cost  # noqa: E402
from radar.scoring.thesis import UNCERTAIN, default_embedder, featurize, load_labels, make_clf  # noqa: E402
from radar.store.db import session_scope  # noqa: E402

LABELS = ROOT / "eval" / "labels"
OUT = ROOT / "eval" / "gates.json"
JUDGE_OUT = LABELS / "thesis_judge_outputs.csv"


def gate(value: Any, target: str, ok: bool | None, method: str, **extra: Any) -> dict[str, Any]:
    return {"value": value, "target": target, "status": "pending" if ok is None else ("pass" if ok else "fail"),
            "method": method, **extra}


def thesis_gate(run_judge: bool) -> dict[str, Any]:
    texts, y = load_labels()
    y_arr = np.array(y)
    X = featurize(texts, default_embedder)
    seeds = (7, 11, 23)
    cv_probs = []
    for seed in seeds:
        skf = StratifiedKFold(5, shuffle=True, random_state=seed)
        cv_probs.append(cross_val_predict(make_clf(), X, y_arr, cv=skf, method="predict_proba")[:, 1])
    clf_acc = [float(((p >= 0.5) == y_arr).mean()) for p in cv_probs]

    judge: list[float] | None = None
    if JUDGE_OUT.exists():
        with open(JUDGE_OUT, newline="") as f:
            judge = [float(r["judge_probability"]) for r in csv.DictReader(f)]
    elif run_judge:
        from radar.agents.judges import ThesisJudge
        with session_scope() as s:
            agent = ThesisJudge(s)
            outs = [agent.run(t) for t in texts]
            judge = [o.output.probability for o in outs]
        with open(JUDGE_OUT, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["id", "label", "judge_probability", "status", "rationale"])
            ids = [r["id"] for r in csv.DictReader(open(LABELS / "thesis_labels.csv", newline=""))]  # noqa: SIM115
            for i, lab, o in zip(ids, y, outs, strict=True):
                w.writerow([i, lab, o.output.probability, o.status, o.output.rationale])

    extra: dict[str, Any] = {"classifier_cv_accuracy_by_seed": clf_acc}
    value = round(float(np.mean(clf_acc)), 3)
    method = "classifier only: stratified 5-fold CV, 3 seeds, 117 labels"
    if judge is not None:
        j = np.array(judge)
        extra["judge_alone_accuracy"] = round(float(((j >= 0.5) == y_arr).mean()), 3)
        combined = []
        for p in cv_probs:
            mix = np.where((p > UNCERTAIN[0]) & (p < UNCERTAIN[1]), j, p)
            combined.append(float(((mix >= 0.5) == y_arr).mean()))
        extra["combined_accuracy_by_seed"] = combined
        extra["uncertain_band"] = list(UNCERTAIN)
        value = round(float(np.mean(combined)), 3)
        method = "shipped path: CV classifier, Haiku judge replaces probabilities in the uncertain band"
    return gate(value, ">= 0.85", value >= 0.85, method, **extra)


def entity_gate() -> dict[str, Any]:
    path = LABELS / "entity_pairs.csv"
    if not path.exists():
        return gate(None, ">= 0.90 precision", None, "100 hand-labeled pairs: not yet labeled")
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    merged = [r for r in rows if r["resolver_decision"] == "merge"]
    tp = sum(1 for r in merged if r["label"] == "same")
    precision = tp / len(merged) if merged else None
    same = [r for r in rows if r["label"] == "same"]
    recall = sum(1 for r in same if r["resolver_decision"] == "merge") / len(same) if same else None
    return gate(round(precision, 3) if precision is not None else None, ">= 0.90 precision",
                None if precision is None else precision >= 0.90,
                f"{len(rows)} labeled pairs; precision over the resolver's {len(merged)} merges",
                recall=round(recall, 3) if recall is not None else None, n_pairs=len(rows))


def claims_gate() -> dict[str, Any]:
    meta_path = LABELS / "claim_spotcheck_meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    spot = LABELS / "claim_spotcheck.csv"
    human = None
    if spot.exists():
        with open(spot, newline="") as f:
            rows = [r for r in csv.DictReader(f) if r.get("human_verdict")]
        if len(rows) >= 40:
            human = round(sum(1 for r in rows if r["human_verdict"].strip().lower() == "supported") / len(rows), 3)
    return gate(human, ">= 0.95 supported (published claims, human spot-check of 40)",
                None if human is None else human >= 0.95,
                "verifier checks every claim; Mohamed adjudicates 40 random published claims",
                memos=meta.get("memos"), claims_checked=meta.get("claims_checked"),
                first_draft_supported_rate=meta.get("first_draft_supported_rate"),
                rewritten=meta.get("rewritten"), removed=meta.get("removed"),
                spotcheck_status="complete" if human is not None else "awaiting Mohamed")


def leakage_gate() -> dict[str, Any]:
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "tests/test_leakage.py"], cwd=ROOT,
                       capture_output=True, text=True)
    ok = r.returncode == 0
    return gate("pass" if ok else "fail", "pass", ok, "tests/test_leakage.py (also in CI)")


def cost_gate() -> dict[str, Any]:
    total = round(cost.ledger_total(), 4)
    return gate(total, "< $15.00", total < 15.0, "append-only ledger data/cost_ledger.jsonl")


def main() -> None:
    run_judge = "--thesis-judge" in sys.argv
    prev = json.loads(OUT.read_text()) if OUT.exists() else {}
    gates = {
        "claim_citation_precision": claims_gate(),
        "thesis_classifier_accuracy": thesis_gate(run_judge),
        "entity_resolution_precision": entity_gate(),
        "leakage_test": leakage_gate(),
        "total_llm_cost": cost_gate(),
        "weekly_pipeline_minutes": prev.get("weekly_pipeline_minutes",
                                            gate(None, "< 20", None, "GitHub Actions log (Phase 5)")),
        "offline_demo": prev.get("offline_demo", gate(None, "make demo on a fresh clone, no keys", None,
                                                      "CI job (Phase 5)")),
    }
    OUT.write_text(json.dumps({"generated": date.today().isoformat(), "gates": gates}, indent=1))
    for k, g in gates.items():
        print(f"{k:30} {g['status']:8} {g['value']}  (target {g['target']})")


if __name__ == "__main__":
    os.environ.setdefault("DATABASE_URL", f"sqlite:///{ROOT / 'data' / 'radar.db'}")
    main()
