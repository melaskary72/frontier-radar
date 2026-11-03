"""Thesis classifier feature experiments on the adjudicated labels (v2).

The candidate list below was fixed before the first run. Each candidate is a feature set plus
a regularization grid. Two estimates per candidate:

- plain CV: stratified 5-fold, three seeds, best C chosen on the same folds (optimistic);
- nested CV: an outer 5-fold split scores a model whose candidate AND C were chosen by an
  inner 5-fold CV on the outer training folds only. This is the honest estimate of the whole
  selection procedure, and it is the "after" number reported in the change log.

No new regex cues were written after seeing errors; the cue flags are the six from v1.
Features use text only, so inference is identical in the live radar and the backtest.

Run: uv run python eval/thesis_experiments.py   (writes eval/thesis_experiments.json)
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.model_selection import StratifiedKFold, cross_val_predict  # noqa: E402

from radar.config import thesis  # noqa: E402
from radar.scoring.thesis import cue_flags, default_embedder, load_labels  # noqa: E402

LABELS = ROOT / "eval" / "labels"
OUT = ROOT / "eval" / "thesis_experiments.json"
SEEDS = (7, 11, 23)
C_GRID = (0.03, 0.1, 0.3, 1.0, 3.0)


def sector_prototypes() -> list[str]:
    """One label-free description per sector, built from config/thesis.yaml."""
    return [f"{name}: " + ", ".join(s.keywords + s.sbir_keywords) for name, s in thesis().sectors.items()]


def _norm(m: np.ndarray) -> np.ndarray:
    return m / np.linalg.norm(m, axis=1, keepdims=True)


def build_blocks(texts: list[str], embed: Callable[[list[str]], np.ndarray]) -> dict[str, np.ndarray]:
    e = _norm(embed(texts))
    p = _norm(embed(sector_prototypes()))
    sims = e @ p.T
    proto = np.hstack([sims, sims.max(axis=1, keepdims=True)])
    return {"E": e, "C": cue_flags(texts), "P": proto}


CANDIDATES = {
    "E+C (v1 shipped)": ["E", "C"],
    "E": ["E"],
    "P": ["P"],
    "P+C": ["P", "C"],
    "E+P": ["E", "P"],
    "E+C+P": ["E", "C", "P"],
}


def clf(c: float) -> LogisticRegression:
    return LogisticRegression(C=c, class_weight="balanced", max_iter=5000)


def cv_acc(X: np.ndarray, y: np.ndarray, c: float, seed: int, folds: int = 5) -> float:
    pred = cross_val_predict(clf(c), X, y, cv=StratifiedKFold(folds, shuffle=True, random_state=seed))
    return float((pred == y).mean())


def run(label_file: str, blocks_by_model: dict[str, dict[str, np.ndarray]], y: np.ndarray) -> dict:
    plain: dict[str, dict] = {}
    for model, blocks in blocks_by_model.items():
        for cand, parts in CANDIDATES.items():
            X = np.hstack([blocks[b] for b in parts])
            by_c = {c: [cv_acc(X, y, c, s) for s in SEEDS] for c in C_GRID}
            v1_c = by_c[1.0]
            best_c = max(by_c, key=lambda c: np.mean(by_c[c]))
            plain[f"{model} | {cand}"] = {"C=1 mean": round(float(np.mean(v1_c)), 3),
                                          "best_C": best_c, "best_C mean": round(float(np.mean(by_c[best_c])), 3),
                                          "best_C by seed": [round(a, 3) for a in by_c[best_c]]}

    # Nested CV over the whole selection procedure (candidate x C), per seed.
    nested, picks = [], []
    keys = [(m, cand) for m in blocks_by_model for cand in CANDIDATES]
    for seed in SEEDS:
        outer = StratifiedKFold(5, shuffle=True, random_state=seed)
        correct = 0
        for tr, te in outer.split(np.zeros(len(y)), y):
            best, best_score = None, -1.0
            for m, cand in keys:
                X = np.hstack([blocks_by_model[m][b] for b in CANDIDATES[cand]])
                for c in C_GRID:
                    inner = StratifiedKFold(5, shuffle=True, random_state=seed + 1)
                    pred = cross_val_predict(clf(c), X[tr], y[tr], cv=inner)
                    score = float((pred == y[tr]).mean())
                    if score > best_score:
                        best, best_score = (m, cand, c), score
            assert best is not None
            m, cand, c = best
            X = np.hstack([blocks_by_model[m][b] for b in CANDIDATES[cand]])
            model = clf(c).fit(X[tr], y[tr])
            correct += int((model.predict(X[te]) == y[te]).sum())
            picks.append(f"{m} | {cand} | C={c}")
        nested.append(round(correct / len(y), 3))
    return {"labels": label_file, "n": len(y), "positives": int(y.sum()), "plain_cv": plain,
            "nested_cv_by_seed": nested, "nested_cv_mean": round(float(np.mean(nested)), 3),
            "nested_picks": sorted(set(picks), key=picks.count, reverse=True),
            "nested_pick_counts": {p: picks.count(p) for p in set(picks)}}


def main() -> None:
    from fastembed import TextEmbedding

    from radar.scoring.thesis import MODEL_CACHE

    base = TextEmbedding("BAAI/bge-base-en-v1.5", cache_dir=str(MODEL_CACHE))

    def base_embed(texts: list[str]) -> np.ndarray:
        return np.array(list(base.embed(texts)))

    out = {"generated": date.today().isoformat(), "candidates": CANDIDATES, "C_grid": list(C_GRID),
           "seeds": list(SEEDS), "results": []}
    for label_file in ("thesis_labels_v1.csv", "thesis_labels.csv"):
        texts, y_list = load_labels(LABELS / label_file)
        y = np.array(y_list)
        blocks = {"bge-small": build_blocks(texts, default_embedder), "bge-base": build_blocks(texts, base_embed)}
        res = run(label_file, blocks, y)
        out["results"].append(res)
        print(label_file, "nested", res["nested_cv_by_seed"], res["nested_cv_mean"], res["nested_pick_counts"])
        for k, v in res["plain_cv"].items():
            print(f"  {k:32} C=1 {v['C=1 mean']:.3f}   best C={v['best_C']}: {v['best_C mean']:.3f}")
    OUT.write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
