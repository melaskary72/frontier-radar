"""Thumbs up or down per company, and `make refit`: propose new group weights.

Refit fits a logistic regression of the verdict on the latest group scores of labeled
companies, maps non-negative coefficients to weights that sum to 1, and appends a before
and after table to docs/weights-history.md. It never edits config/weights.yaml: a person
reads the table and decides.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import numpy as np
from sklearn.linear_model import LogisticRegression
from sqlalchemy import select
from sqlalchemy.orm import Session

from radar.config import REPO_ROOT, weights
from radar.store.models import FeedbackRow, Score

HISTORY = REPO_ROOT / "docs" / "weights-history.md"
MIN_LABELS = 10


def add_feedback(session: Session, company_id: int, verdict: int, user_label: str = "partner") -> FeedbackRow:
    if verdict not in (1, -1):
        raise ValueError("verdict must be +1 or -1")
    row = FeedbackRow(company_id=company_id, verdict=verdict, user_label=user_label, created_at=datetime.now(UTC))
    session.add(row)
    session.flush()
    return row


def propose_weights(session: Session) -> tuple[dict[str, float], dict[str, float], int] | None:
    current = {g: float(v["weight"]) for g, v in weights()["groups"].items()}
    latest: dict[int, int] = {}
    for fb in session.scalars(select(FeedbackRow).order_by(FeedbackRow.created_at)):
        latest[fb.company_id] = fb.verdict
    X, y = [], []
    for cid, verdict in latest.items():
        sc = session.scalars(select(Score).where(Score.company_id == cid).order_by(Score.as_of.desc())).first()
        if sc is None:
            continue
        groups = sc.breakdown_json.get("groups", {})
        gw = sc.breakdown_json.get("group_weights", {})
        # undo the weighting so the regression sees raw 0..1 group scores
        X.append([groups.get(g, 0.0) / gw[g] if gw.get(g) else 0.0 for g in current])
        y.append(1 if verdict > 0 else 0)
    if len(y) < MIN_LABELS or len(set(y)) < 2:
        return None
    clf = LogisticRegression(max_iter=1000).fit(np.array(X), np.array(y))
    coef = np.clip(clf.coef_[0], 0, None)
    if coef.sum() == 0:
        return None
    proposed = {g: round(float(c / coef.sum()), 3) for g, c in zip(current, coef, strict=True)}
    return current, proposed, len(y)


def write_history(current: dict[str, float], proposed: dict[str, float], n: int, on: date | None = None) -> str:
    on = on or date.today()
    lines = [f"\n## {on.isoformat()}: proposal from {n} labeled companies\n",
             "| Group | Current | Proposed | Change |", "|---|---|---|---|"]
    for g in current:
        lines.append(f"| {g} | {current[g]:.2f} | {proposed[g]:.2f} | {proposed[g] - current[g]:+.2f} |")
    lines.append("\nNot applied. To adopt, edit config/weights.yaml and note it in docs/09-change-log.md.\n")
    text = "\n".join(lines)
    if not HISTORY.exists():
        HISTORY.write_text("# Weights History\n\nProposals from `make refit`. None are applied automatically.\n")
    with open(HISTORY, "a") as f:
        f.write(text)
    return text
