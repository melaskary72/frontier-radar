"""USAspending.gov award search: DoD, DOE, and NASA contracts and grants to small businesses."""

from __future__ import annotations

from datetime import date
from typing import Any

from radar.collectors.base import HttpCollector
from radar.collectors.common import parse_date, record_discovery, text_has_any, upsert
from radar.config import sources, thesis
from radar.store.models import Award

FIELDS = ["Award ID", "Recipient Name", "Award Amount", "Start Date", "Awarding Agency",
          "Awarding Sub Agency", "Description", "generated_internal_id"]
CONTRACT_CODES = ["A", "B", "C", "D"]
GRANT_CODES = ["02", "03", "04", "05"]


class USAspendingCollector(HttpCollector):
    source = "usaspending"

    def search(self, keywords: list[str], since: date, until: date, *, award_codes: list[str],
               limit: int = 100, max_pages: int = 2) -> list[tuple[dict[str, Any], int]]:
        url = f"{sources()['endpoints']['usaspending']}/search/spending_by_award/"
        agencies = [{"type": "awarding", "tier": "toptier", "name": n}
                    for n in sources()["federal"]["usaspending_agencies"]]
        out: list[tuple[dict[str, Any], int]] = []
        for page in range(1, max_pages + 1):
            body = {
                "filters": {
                    "keywords": keywords,
                    "time_period": [{"start_date": since.isoformat(), "end_date": until.isoformat()}],
                    "award_type_codes": award_codes,
                    "agencies": agencies,
                    "recipient_type_names": ["small_business"],
                },
                "fields": FIELDS, "limit": limit, "page": page,
                "sort": "Award Amount", "order": "desc",
            }
            f = self.fetch(url, method="POST", json_body=body, ok_statuses=(200, 400, 422))
            results = (f.payload or {}).get("results", []) if f.status == 200 else []
            out.extend((r, f.snapshot_id) for r in results)
            if not (f.payload or {}).get("page_metadata", {}).get("hasNext"):
                break
        return out

    def discover(self, sector: str, since: date, until: date | None = None) -> list[Award]:
        until = until or date.today()
        kws = thesis().sectors[sector].sbir_keywords
        awards: dict[str, Award] = {}
        for codes in (CONTRACT_CODES, GRANT_CODES):
            for r, snap in self.search(kws, since, until, award_codes=codes):
                row = self.upsert_award(r, snap)
                record_discovery(self.session, "awards", row.id, sector, f"usaspending:{kws}")
                awards[row.external_id] = row
            # SBIR and STTR awards, kept only if the description also hits a thesis keyword.
            for r, snap in self.search(["SBIR", "STTR"], since, until, award_codes=codes):
                if text_has_any(r.get("Description") or "", kws + thesis().sectors[sector].keywords):
                    row = self.upsert_award(r, snap, program="SBIR/STTR")
                    record_discovery(self.session, "awards", row.id, sector, "usaspending:SBIR,STTR")
                    awards[row.external_id] = row
        return list(awards.values())

    def upsert_award(self, r: dict[str, Any], snap: int, program: str | None = None) -> Award:
        gid = r.get("generated_internal_id") or r.get("Award ID")
        return upsert(self.session, Award, {"source": "usaspending", "external_id": str(gid)}, {
            "recipient": r.get("Recipient Name") or "",
            "agency": " / ".join(x for x in [r.get("Awarding Agency"), r.get("Awarding Sub Agency")] if x),
            "program": program,
            "amount": r.get("Award Amount"),
            "award_date": parse_date(r.get("Start Date")),
            "topic": r.get("Description"),
            "url": f"https://www.usaspending.gov/award/{gid}" if gid else None,
            "snapshot_id": snap,
        })
