"""Thesis-fit classifier: is this company building substantive technology in the thesis?

Sentence embeddings (BAAI/bge-small-en-v1.5 through fastembed, ONNX, runs on a laptop CPU)
plus six rubric cue flags, into a class-balanced logistic regression trained on the
hand-labeled examples in eval/labels/thesis_labels.csv. Rubric: eval/labels/README.md.

Probabilities inside UNCERTAIN go to an LLM judge when one is configured (Phase 3). The
gate (at least 85% accuracy) is measured on the full classifier-plus-judge path.

Measured on 2026-11-01, 117 labels, stratified 5-fold CV over three seeds:
  TF-IDF words and characters      62%
  bge-small embeddings             66 to 68%
  bge-base embeddings              67 to 73%
  bge-small embeddings plus cues   71 to 74%   <- shipped (lighter than bge-base, slightly better)
"""

from __future__ import annotations

import csv
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from functools import cache
from pathlib import Path
from typing import Protocol

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sqlalchemy import select
from sqlalchemy.orm import Session

from radar.collectors.common import clean_text
from radar.config import REPO_ROOT
from radar.store.models import Award, Company, Edge, HNPost, Paper, Repo

LABELS = REPO_ROOT / "eval" / "labels" / "thesis_labels.csv"
MODEL_NAME = "BAAI/bge-small-en-v1.5"
MODEL_CACHE = REPO_ROOT / "data" / "cache" / "models"
UNCERTAIN = (0.3, 0.7)

# Rubric cues (eval/labels/README.md), written before any cross-validation run.
CUES = {
    "education": r"\b(course|courses|lessons?|tutorial|bootcamp|workshop|hackathon|homework|assignment|"
                 r"learning project|educational|case study|cookbook|templates?|student)\b",
    "simulation": r"\b(simulat\w*|visuali[sz]\w*|demo|playground|game)\b",
    "listing": r"\b(map|registry|list|directory|download|installer|replica|port of|notes)\b",
    "commodity": r"\b(supplies|warranty|parking|labor|training|refill|purchase|procure\w*|sustainment)\b",
    "research": r"\b(official (code|implementation)|neurips|iclr|icra|iros|corl|eccv|cvpr|arxiv)\b",
    "build": r"\b(firmware|controller|stack|platform|engine|accelerator|production|hardware|asic|fpga|"
             r"actuators?|propulsion|reactor|uas|unmanned|autonomous)\b",
}
_CUE_RE = [re.compile(p, re.I) for p in CUES.values()]

LLMJudge = Callable[[str], float]


class Embedder(Protocol):
    def __call__(self, texts: Sequence[str]) -> np.ndarray: ...


@cache
def _fastembed():  # type: ignore[no-untyped-def]
    from fastembed import TextEmbedding

    MODEL_CACHE.mkdir(parents=True, exist_ok=True)
    return TextEmbedding(MODEL_NAME, cache_dir=str(MODEL_CACHE))


def default_embedder(texts: Sequence[str]) -> np.ndarray:
    return np.array(list(_fastembed().embed(list(texts))))


def cue_flags(texts: Sequence[str]) -> np.ndarray:
    return np.array([[1.0 if r.search(t) else 0.0 for r in _CUE_RE] for t in texts])


def featurize(texts: Sequence[str], embed: Embedder) -> np.ndarray:
    return np.hstack([embed(texts), cue_flags(texts)])


def make_clf() -> LogisticRegression:
    return LogisticRegression(C=1.0, class_weight="balanced", max_iter=3000)


def load_labels(path: Path = LABELS) -> tuple[list[str], list[int]]:
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    return [r["text"] for r in rows], [int(r["label"]) for r in rows]


@dataclass
class CVReport:
    n: int
    accuracy: float
    precision: float
    recall: float
    fold_accuracies: list[float]


def cross_validate(embed: Embedder = default_embedder, path: Path = LABELS, folds: int = 5,
                   seed: int = 7) -> CVReport:
    texts, y = load_labels(path)
    X, y_arr = featurize(texts, embed), np.array(y)
    skf = StratifiedKFold(n_splits=folds, shuffle=True, random_state=seed)
    pred = cross_val_predict(make_clf(), X, y_arr, cv=skf)
    tp = int(((pred == 1) & (y_arr == 1)).sum())
    fp = int(((pred == 1) & (y_arr == 0)).sum())
    fn = int(((pred == 0) & (y_arr == 1)).sum())
    folds_acc = [float((pred[t] == y_arr[t]).mean()) for _, t in skf.split(X, y_arr)]
    return CVReport(len(y), float((pred == y_arr).mean()), tp / max(tp + fp, 1), tp / max(tp + fn, 1), folds_acc)


class ThesisClassifier:
    def __init__(self, embed: Embedder = default_embedder, labels: Path = LABELS):
        self.embed = embed
        texts, y = load_labels(labels)
        self.clf = make_clf().fit(featurize(texts, embed), np.array(y))
        self._cache: dict[str, float] = {}  # text -> classifier probability

    def predict(self, texts: Sequence[str], judge: LLMJudge | None = None) -> list[tuple[float, str]]:
        """(probability, method) per text; method is 'classifier' or 'llm'."""
        if not texts:
            return []
        todo = [t for t in dict.fromkeys(texts) if t not in self._cache]
        if todo:
            for t, p in zip(todo, self.clf.predict_proba(featurize(todo, self.embed))[:, 1], strict=True):
                self._cache[t] = float(p)
        probs = [self._cache[t] for t in texts]
        out = []
        for text, p in zip(texts, probs, strict=True):
            if judge is not None and UNCERTAIN[0] < p < UNCERTAIN[1]:
                out.append((float(judge(text)), "llm"))
            else:
                out.append((float(p), "classifier"))
        return out


@cache
def default_classifier() -> ThesisClassifier:
    return ThesisClassifier()


def thesis_probability(texts: list[str], judge: LLMJudge | None = None,
                       classifier: ThesisClassifier | None = None) -> list[tuple[float, str]]:
    return (classifier or default_classifier()).predict(texts, judge)


def company_text(session: Session, company: Company, as_of: date | None = None) -> str:
    """Name plus every description the company's evidence carries, in label format.

    With as_of, only rows timestamped on or before that date are included, so a backtest
    never classifies a company using text it published later."""
    end = datetime.combine(as_of, time.max, tzinfo=UTC) if as_of else None
    parts = [company.name]
    links: dict[str, list[int]] = {}
    for e in session.scalars(select(Edge).where(Edge.src_type == "company", Edge.src_id == company.id)):
        links.setdefault(e.dst_type, []).append(e.dst_id)
    repo_q = select(Repo).where(Repo.id.in_(links.get("repos", [])))
    hn_q = select(HNPost).where(HNPost.id.in_(links.get("hn_posts", [])))
    award_q = select(Award).where(Award.id.in_(links.get("awards", [])))
    if end is not None:
        repo_q, hn_q = repo_q.where(Repo.created_at <= end), hn_q.where(HNPost.created_at <= end)
        award_q = award_q.where(Award.award_date <= as_of)
    repos = list(session.scalars(repo_q))
    for r in repos:
        parts += [r.full_name.split("/")[-1].replace("-", " "), r.description or "", " ".join(r.topics_json or [])]
    parts += [p.title for p in session.scalars(hn_q)]
    parts += [a.topic or "" for a in session.scalars(award_q)]
    if repos:
        pids = list(session.scalars(select(Edge.dst_id).where(Edge.relation == "implements", Edge.src_type == "repos",
                                                              Edge.src_id.in_([r.id for r in repos]))))
        paper_q = select(Paper).where(Paper.id.in_(pids))
        if end is not None:
            paper_q = paper_q.where(Paper.submitted_at <= end)
        parts += [pap.title for pap in session.scalars(paper_q)]
    return clean_text(" ".join(x for x in parts if x))[:1200]
