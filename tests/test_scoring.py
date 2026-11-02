"""Point-in-time features, percentile scoring, explanations, thesis classifier, refit."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from sqlalchemy.orm import Session

from radar.scoring import features as F
from radar.scoring import feedback
from radar.scoring.explain import explain
from radar.scoring.score import percentile_frame, score_companies
from radar.scoring.thesis import ThesisClassifier, cue_flags
from radar.store.models import Company, Edge, Filing, Repo, RepoEvent, RepoTree

T = date(2026, 6, 30)


def dt(d: date, h: int = 12) -> datetime:
    return datetime(d.year, d.month, d.day, h, tzinfo=UTC)


def make_company(session: Session, name: str, forks_before: int, forks_after: int,
                 form_d: date | None = None) -> Company:
    c = Company(entity_key=f"name:{name}", name=name, kind="org", sector="robotics", first_seen_at=dt(T - timedelta(days=200)))
    repo = Repo(full_name=f"{name}/core", owner=name, owner_type="Organization", created_at=dt(T - timedelta(days=200)),
                topics_json=[])
    session.add_all([c, repo])
    session.flush()
    session.add(Edge(src_type="company", src_id=c.id, dst_type="repos", dst_id=repo.id, relation="has_evidence",
                     confidence=1.0, method="deterministic"))
    for i in range(forks_before):
        session.add(RepoEvent(repo_full_name=repo.full_name, kind="fork", external_id=f"b{i}", actor=f"u{i}",
                              is_external=True, at=dt(T - timedelta(days=5 + i % 20))))
    for i in range(forks_after):  # after the cutoff: must never count
        session.add(RepoEvent(repo_full_name=repo.full_name, kind="fork", external_id=f"a{i}", actor=f"v{i}",
                              is_external=True, at=dt(T + timedelta(days=1 + i))))
    session.add(RepoTree(repo_full_name=repo.full_name, sha="abc1234", committed_at=dt(T - timedelta(days=3)),
                         signals_json={"has_tests": True, "has_ci": True, "has_license": True,
                                       "readme_bytes": 4000, "code_files": 10, "hardware_files": 5}))
    session.add(RepoTree(repo_full_name=repo.full_name, sha="fff9999", committed_at=dt(T + timedelta(days=3)),
                         signals_json={"has_tests": False, "has_ci": False, "has_license": False,
                                       "readme_bytes": 0, "code_files": 10, "hardware_files": 0}))
    if form_d:
        f = Filing(accession=f"acc-{name}", cik="1", company_name=name, form_type="D", filed_at=form_d, url="u")
        session.add(f)
        session.flush()
        session.add(Edge(src_type="company", src_id=c.id, dst_type="filings", dst_id=f.id, relation="has_evidence",
                         confidence=0.9, method="deterministic"))
    session.flush()
    return c


def test_features_are_point_in_time(session: Session) -> None:
    c = make_company(session, "alpha", forks_before=6, forks_after=50, form_d=T + timedelta(days=10))
    vals = F.compute(F.load_company_data(session, c, T), pit_only=True)
    assert vals["fork_velocity_30d"].value == pytest.approx(6 / 30)
    assert vals["repo_quality"].value == pytest.approx(1.0)       # tree before T, not after
    assert vals["hardware_code_share"].value == pytest.approx(0.5)
    assert vals["no_form_d"].value == 1.0                          # Form D is after T
    later = F.compute(F.load_company_data(session, c, T + timedelta(days=30)), pit_only=True)
    assert later["no_form_d"].value == 0.0


def test_pit_only_drops_unobservable_features(session: Session) -> None:
    c = make_company(session, "beta", 1, 0)
    data = F.load_company_data(session, c, T)
    pit = F.compute(data, pit_only=True)
    live = F.compute(data, pit_only=False)
    assert {"star_velocity_30d", "hiring_velocity", "log_citations", "h_index"} & set(pit) == set()
    assert {"star_velocity_30d", "hiring_velocity"} <= set(live)


def test_percentiles_zero_means_no_credit() -> None:
    raw = pd.DataFrame({"x": [0.0, 1.0, 2.0, np.nan]}, index=[1, 2, 3, 4])
    pct = percentile_frame(raw, pd.Series({1: "a", 2: "a", 3: "a", 4: "a"}))
    assert pct.loc[1, "x"] == 0 and pct.loc[4, "x"] == 0
    assert pct.loc[3, "x"] == 1.0 and 0 < pct.loc[2, "x"] < 1


class FakeEmbedder:
    def __call__(self, texts):  # deterministic toy embedding: robot-ness and tutorial-ness
        return np.array([[t.lower().count("robot"), t.lower().count("tutorial")] for t in texts], dtype=float)


def test_scoring_ranks_and_explains(session: Session) -> None:
    hot = make_company(session, "hot", forks_before=20, forks_after=0)
    cold = make_company(session, "cold", forks_before=1, forks_after=0, form_d=T - timedelta(days=30))
    thesis = {hot.id: (0.9, "classifier"), cold.id: (0.9, "classifier")}
    results = score_companies(session, [hot, cold], T, pit_only=True, thesis=thesis)
    assert [r.company_id for r in results] == [hot.id, cold.id]
    assert abs(sum(results[0].groups.values()) - results[0].total) < 1e-9
    ex = explain(results[0])
    assert ex.top_evidence and ex.top_evidence[0]["label"]
    assert len(ex.top_evidence) <= 3


def test_thesis_classifier_with_injected_embedder(tmp_path: Path) -> None:
    labels = tmp_path / "labels.csv"
    rows = ["id,source,text,label,labeled_by,note"]
    for i in range(10):
        rows.append(f"{i},repo,robot arm controller {i},1,test,")
        rows.append(f"{i + 10},repo,python tutorial course {i},0,test,")
    labels.write_text("\n".join(rows) + "\n")
    clf = ThesisClassifier(embed=FakeEmbedder(), labels=labels)
    (p_robot, method), (p_tut, _) = clf.predict(["robot gripper firmware", "a tutorial for beginners"])
    assert method == "classifier" and p_robot > 0.5 > p_tut
    assert cue_flags(["hackathon project"])[0][0] == 1.0


def test_refit_proposes_but_never_applies(session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(feedback, "HISTORY", tmp_path / "weights-history.md")
    assert feedback.propose_weights(session) is None  # too few labels
    from radar.store.models import Score
    rng = np.random.default_rng(0)
    for i in range(12):
        c = Company(entity_key=f"k{i}", name=f"c{i}", kind="org", first_seen_at=dt(T))
        session.add(c)
        session.flush()
        mom = float(rng.uniform(0.6, 1.0) if i % 2 else rng.uniform(0, 0.3))
        session.add(Score(company_id=c.id, as_of=T, total=mom, rank=i + 1,
                          breakdown_json={"groups": {"momentum": mom * 0.25, "thesis_fit": 0.1},
                                          "group_weights": {"momentum": 0.25, "thesis_fit": 0.15}}))
        feedback.add_feedback(session, c.id, 1 if i % 2 else -1)
    out = feedback.propose_weights(session)
    assert out is not None
    current, proposed, n = out
    assert n == 12 and abs(sum(proposed.values()) - 1) < 0.01
    assert proposed["momentum"] > current["momentum"]
    text = feedback.write_history(current, proposed, n)
    assert "Not applied" in text and (tmp_path / "weights-history.md").exists()
