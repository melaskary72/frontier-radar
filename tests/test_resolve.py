from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from radar.entities.resolve import (
    Mention,
    compare,
    is_academic,
    is_incumbent,
    loose_name,
    name_from_hn_title,
    normalize_domain,
    resolve,
    strict_name,
)
from radar.store.models import Award, Company, Discovery, Edge, EntityCandidate, HNPost, Repo

NOW = datetime(2026, 10, 1, tzinfo=UTC)
EN = chr(0x2013)  # Launch HN titles use an en dash between name and tagline


def m(key: str, source: str, name: str, **kw) -> Mention:
    return Mention(key=key, source=source, name=name, kind=kw.pop("kind", "org"), **kw)


def test_normalization() -> None:
    assert strict_name("ACME ROBOTICS, INC.") == "acme robotics"
    assert loose_name("Acme Robotics Labs LLC") == "acme"
    assert normalize_domain("https://www.acme.ai/about") == "acme.ai"
    assert normalize_domain("https://github.com/acme") is None
    assert normalize_domain("https://acme.github.io") is None
    assert normalize_domain("https://doi.org/10.1/x") is None


def test_hn_title_names() -> None:
    assert name_from_hn_title(f"Launch HN: Nori Robotics (YC S26) {EN} A low-cost humanoid") == "Nori Robotics"
    assert name_from_hn_title(f"Show HN: Sonny OS {EN} An async Rust microkernel") == "Sonny OS"
    assert name_from_hn_title("Show HN: I built a drone tracker") is None


def test_compare_tiers() -> None:
    a = m("gh:acme", "github", "acme-robotics", github_login="acme-robotics", domain="acme.ai")
    assert compare(a, m("hn:1", "hn", "Acme Robotics", domain="acme.ai")).verdict == "merge"
    assert compare(a, m("hn:2", "hn", "Acme Robotics", github_login="acme-robotics")).verdict == "merge"
    # Same distinctive name, nothing conflicting: deterministic merge.
    d = compare(m("award:x", "award", "Kestrel Propulsion Systems"), m("hn:3", "hn", "Kestrel Propulsion Systems"))
    assert d.verdict == "merge" and d.method == "deterministic"
    # Same short name: ambiguous, goes to a judge or the queue.
    assert compare(m("hn:4", "hn", "Orbit"), m("hn:5", "hn", "Orbit")).verdict == "ambiguous"
    # Same name but different domains: different companies.
    assert compare(m("hn:6", "hn", "Orbit", domain="a.com"), m("hn:7", "hn", "Orbit", domain="b.com")).verdict == "separate"
    # Unrelated names are not compared at all.
    assert compare(m("hn:8", "hn", "Helix Grid"), m("hn:9", "hn", "Quanta Foundry")) is None


def test_heuristics() -> None:
    assert is_academic("Tsinghua-MARS-Lab", "tsinghua-mars-lab", None)
    assert is_academic("Vision Group", None, "vision.cs.example.edu")
    assert not is_academic("8-bit-robotics", "8-bit-robotics", None)
    assert is_incumbent("NVIDIA", "nvidia") and not is_incumbent("Teslabot", "teslabot")


def _seed(session: Session) -> None:
    snap = None
    r = Repo(full_name="kestrel-prop/engine", owner="kestrel-prop", owner_type="Organization",
             description="Rotating detonation engine controller", created_at=NOW, stars=10, forks=2,
             topics_json=["propulsion"], homepage="https://kestrelprop.com", snapshot_id=snap)
    session.add_all([
        r,
        HNPost(object_id="1", kind="launch_hn", title=f"Launch HN: Kestrel Propulsion (YC S26) {EN} RDEs",
               url="https://kestrelprop.com", points=50, num_comments=10, created_at=NOW),
        Award(source="usaspending", external_id="A1", recipient="KESTREL PROPULSION, INC.",
              agency="DOD", amount=1e6, award_date=date(2026, 9, 1), topic="RDE prototype"),
        HNPost(object_id="2", kind="show_hn", title=f"Show HN: Orbit {EN} a calendar", url=None,
               points=3, num_comments=0, created_at=NOW),
        HNPost(object_id="3", kind="show_hn", title=f"Show HN: Orbit {EN} a satellite viewer", url=None,
               points=3, num_comments=0, created_at=NOW),
    ])
    session.flush()
    session.add(Discovery(table_name="repos", row_id=r.id, sector="space", query="topic:propulsion",
                          discovered_at=NOW))
    session.flush()


def test_resolve_end_to_end(session: Session) -> None:
    _seed(session)
    res = resolve(session)
    companies = {c.name: c for c in session.scalars(select(Company))}
    # Repo + launch merge on domain; the award joins on the distinctive name.
    k = companies["Kestrel Propulsion"]
    assert k.domain == "kestrelprop.com" and k.sector == "space"
    linked = {e.dst_type for e in session.scalars(select(Edge).where(Edge.src_type == "company", Edge.src_id == k.id))}
    assert linked == {"repos", "hn_posts", "awards"}
    # The two "Orbit" posts are queued, not merged.
    assert res.queued == 1
    assert sum(1 for c in session.scalars(select(Company)) if c.name == "Orbit") == 2


def test_human_rejection_is_respected(session: Session) -> None:
    _seed(session)
    resolve(session)
    cand = session.scalars(select(EntityCandidate)).one()
    cand.status, cand.decided_by = "approved", "human"
    resolve(session)
    assert sum(1 for c in session.scalars(select(Company)) if c.name == "Orbit") == 1
    cand.status = "rejected"
    resolve(session)
    assert sum(1 for c in session.scalars(select(Company)) if c.name == "Orbit") == 2


def test_resolve_is_idempotent(session: Session) -> None:
    _seed(session)
    resolve(session)
    ids = sorted(c.id for c in session.scalars(select(Company)))
    resolve(session)
    assert sorted(c.id for c in session.scalars(select(Company))) == ids
