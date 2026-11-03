"""Live Picks selection: companies only, no labs or incumbents, no Form D on file."""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy.orm import Session

from radar.picks import select_picks, write_picks
from radar.store.models import Company, Score, WebPage

AS_OF = date(2026, 11, 3)


def add(session: Session, rank: int, name: str, kind: str, domain: str | None = None, why: str = "") -> None:
    c = Company(entity_key=f"name:{name}", name=name, kind=kind, domain=domain, sector="robotics",
                first_seen_at=datetime(2026, 6, 1, tzinfo=UTC))
    session.add(c)
    session.flush()
    session.add(Score(company_id=c.id, as_of=AS_OF, total=1 - rank / 100, rank=rank,
                      breakdown_json={"groups": {}, "why_now": why, "top_evidence": [{"label": f"{name} repo"}]}))


def test_selection_rules(session: Session, tmp_path) -> None:  # type: ignore[no-untyped-def]
    add(session, 1, "Example Vision Group", "lab")
    add(session, 2, "someone-personal", "user")
    add(session, 3, "Funded Robotics Inc", "org", "funded.ai", "already raised")
    add(session, 4, "Example Actuators", "org", "example-actuators.ai", "shipped a new arm")
    add(session, 5, "lone-hacker", "user", "lonehacker.dev")
    session.flush()
    add(session, 6, "No Site Robotics", "org")
    for d in ("example-actuators.ai", "lonehacker.dev"):
        session.add(WebPage(domain=d, url=f"https://{d}/", page_kind="home", text="We build robots for factories.",
                            fetched_at=datetime(2026, 11, 3, tzinfo=UTC)))
    session.flush()
    picks, skipped = select_picks(session, AS_OF, 5, formd={"funded robotics"},
                                  why_now=lambda c, s: f"written for {c.name} (evidence 1, 2021)")
    assert [p.name for p in picks] == ["Example Actuators", "lone-hacker"]
    assert picks[0].why_now == "shipped a new arm" and picks[1].why_now == "written for lone-hacker"
    assert skipped["individual"] == 1 and skipped["lab_or_incumbent"] == 1 and skipped["has_form_d"] == 1
    assert skipped["no_company_website"] == 1
    jp, mp = write_picks(picks, AS_OF, skipped, "2026-11-02", tmp_path)
    assert "2027-05-04" in mp.read_text() and jp.exists()


def test_model_prose_glitches_are_cleaned() -> None:
    from radar.collectors.common import clean_model_prose, clean_text
    from radar.picks import clean_why_now

    assert clean_model_prose("created 2026-10-12 and\u5df3 reached 290 stars") == "created 2026-10-12 and reached 290 stars"
    assert clean_why_now("1,145 stars (evidence 1, 2021). Strong signal.") == "1,145 stars. Strong signal."
    assert clean_text("\u98de\u7279 HD1910") == "\u98de\u7279 HD1910"  # scraped CJK text is kept
