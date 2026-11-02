"""SBIR.gov public awards API: DoD, DOE, and NASA Small Business Innovation Research awards.

AFWERX awards appear as DoD awards with branch "Air Force".

Status (November 2026): every api.www.sbir.gov endpoint returns 403 Forbidden regardless of
client. The collector records the 403, sets `available = False`, and returns no rows; SBIR
and STTR awards are still covered through USAspending (keywords "SBIR" and "STTR").
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from radar.collectors.base import HttpCollector
from radar.collectors.common import parse_date, upsert
from radar.config import sources, thesis
from radar.store.models import Award

log = logging.getLogger(__name__)


def award_external_id(a: dict[str, Any]) -> str:
    return str(a.get("contract") or a.get("agency_tracking_number")
               or f"{a.get('firm')}|{a.get('proposal_award_date')}|{a.get('award_title')}")


class SBIRCollector(HttpCollector):
    source = "sbir"
    available = True

    def search(self, keyword: str, agency: str | None = None, year: int | None = None,
               rows: int = 100, max_pages: int = 2) -> list[tuple[dict[str, Any], int]]:
        url = sources()["endpoints"]["sbir"]
        out: list[tuple[dict[str, Any], int]] = []
        if not self.available:
            return out
        for page in range(max_pages):
            f = self.fetch(url, params={"keyword": keyword, "agency": agency, "year": year,
                                        "rows": rows, "start": page * rows},
                           ok_statuses=(200, 403, 404))
            if f.status == 403:
                log.warning("SBIR.gov API returned 403; marking source unavailable for this run")
                self.available = False
                break
            items = f.payload if f.status == 200 and isinstance(f.payload, list) else []
            out.extend((a, f.snapshot_id) for a in items)
            if len(items) < rows:
                break
        return out

    def discover(self, sector: str, since: date, until: date | None = None) -> list[Award]:
        until = until or date.today()
        awards: dict[str, Award] = {}
        for kw in thesis().sectors[sector].sbir_keywords:
            for agency in sources()["federal"]["sbir_agencies"]:
                for year in range(since.year, until.year + 1):
                    for a, snap in self.search(kw, agency=agency, year=year):
                        d = parse_date(a.get("proposal_award_date"))
                        if d is None or not (since <= d <= until):
                            continue
                        row = self.upsert_award(a, snap)
                        awards[row.external_id] = row
        return list(awards.values())

    def upsert_award(self, a: dict[str, Any], snap: int) -> Award:
        return upsert(self.session, Award, {"source": "sbir", "external_id": award_external_id(a)}, {
            "recipient": a.get("firm") or "",
            "agency": " / ".join(x for x in [a.get("agency"), a.get("branch")] if x),
            "program": f"{a.get('program') or 'SBIR'} {a.get('phase') or ''}".strip(),
            "amount": float(a["award_amount"]) if a.get("award_amount") not in (None, "") else None,
            "award_date": parse_date(a.get("proposal_award_date")),
            "topic": a.get("award_title") or a.get("topic_code"),
            "url": a.get("award_link") or a.get("company_url"),
            "snapshot_id": snap,
        })
