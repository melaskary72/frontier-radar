"""Public job boards: Greenhouse, Lever, Ashby.

For each discovered company we probe likely board slugs on all three providers, then track
postings over time (first_seen_at / last_seen_at). ATS history is not available
point-in-time from these APIs, so the backtest marks hiring features unavailable.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from radar.collectors.base import HttpCollector
from radar.collectors.common import text_has_any
from radar.config import sources
from radar.store.models import JobPosting


def slug_candidates(name: str, domain: str | None = None) -> list[str]:
    base = re.sub(r"[^a-z0-9 ]", "", name.lower())
    words = base.split()
    cands = ["".join(words), "-".join(words)]
    if domain:
        cands.append(domain.split(".")[0].lower())
    if words and words[-1] in {"inc", "labs", "systems", "technologies", "ai", "robotics"}:
        cands.append("".join(words[:-1]))
    seen: list[str] = []
    for c in cands:
        if c and c not in seen:
            seen.append(c)
    return seen


def normalize(provider: str, payload: Any) -> list[dict[str, Any]]:
    """Map each provider's JSON into {external_id, title, department, location}."""
    if provider == "greenhouse":
        return [{
            "external_id": str(j["id"]), "title": j.get("title", ""),
            "department": ", ".join(d["name"] for d in j.get("departments", []) or []) or None,
            "location": (j.get("location") or {}).get("name"),
        } for j in (payload or {}).get("jobs", [])]
    if provider == "lever":
        return [{
            "external_id": j["id"], "title": j.get("text", ""),
            "department": (j.get("categories") or {}).get("department")
            or (j.get("categories") or {}).get("team"),
            "location": (j.get("categories") or {}).get("location"),
        } for j in (payload or [])]
    if provider == "ashby":
        return [{
            "external_id": j["id"], "title": j.get("title", ""),
            "department": j.get("department") or j.get("team"),
            "location": j.get("location"),
        } for j in (payload or {}).get("jobs", [])]
    raise ValueError(provider)


class ATSCollector(HttpCollector):
    source = "ats"

    def board_url(self, provider: str, slug: str) -> tuple[str, dict[str, Any]]:
        ep = sources()["endpoints"]
        if provider == "greenhouse":
            return f"{ep['greenhouse']}/{slug}/jobs", {}
        if provider == "lever":
            return f"{ep['lever']}/{slug}", {"mode": "json"}
        return f"{ep['ashby']}/{slug}", {"includeCompensation": "false"}

    def fetch_board(self, provider: str, slug: str) -> tuple[list[dict[str, Any]] | None, int]:
        url, params = self.board_url(provider, slug)
        f = self.fetch(url, params=params, ok_statuses=(200, 404))
        if f.status != 200:
            return None, f.snapshot_id
        return normalize(provider, f.payload), f.snapshot_id

    def find_board(self, name: str, domain: str | None = None) -> tuple[str, str] | None:
        for slug in slug_candidates(name, domain):
            for provider in sources()["ats"]["providers"]:
                jobs, _ = self.fetch_board(provider, slug)
                if jobs is not None:
                    return provider, slug
        return None

    def track(self, provider: str, slug: str, company_id: int | None = None) -> list[JobPosting]:
        jobs, snap = self.fetch_board(provider, slug)
        if jobs is None:
            return []
        cfg = sources()["ats"]
        now = datetime.now(UTC)
        out = []
        for j in jobs:
            stmt = select(JobPosting).filter_by(ats=provider, board=slug, external_id=j["external_id"])
            row = self.session.scalars(stmt).first()
            dept = (j.get("department") or "")
            if row is None:
                row = JobPosting(ats=provider, board=slug, external_id=j["external_id"],
                                 first_seen_at=now, company_id=company_id)
                self.session.add(row)
            row.title = j["title"]
            row.department = j.get("department")
            row.location = j.get("location")
            row.is_engineering = text_has_any(f"{dept} {j['title']}", cfg["engineering_departments"] + ["engineer"])
            row.is_hardware = text_has_any(j["title"], cfg["hardware_title_terms"])
            row.last_seen_at = now
            row.snapshot_id = snap
            out.append(row)
        self.session.flush()
        return out
