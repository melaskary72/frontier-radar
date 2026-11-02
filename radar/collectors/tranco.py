"""Tranco domain ranks. The ranks API returns a dated history per domain, which makes
web traction a point-in-time feature (rank on or before the cutoff date)."""

from __future__ import annotations

from datetime import date

from radar.collectors.base import HttpCollector
from radar.collectors.common import parse_date, upsert
from radar.config import sources
from radar.store.models import DomainRank


class TrancoCollector(HttpCollector):
    source = "tranco"

    def ranks(self, domain: str) -> list[DomainRank]:
        f = self.fetch(f"{sources()['endpoints']['tranco']}/ranks/domain/{domain}",
                       ok_statuses=(200, 404))
        out = []
        for r in (f.payload or {}).get("ranks", []) if f.status == 200 else []:
            d = parse_date(r.get("date"))
            if d is None or r.get("rank") is None:
                continue
            out.append(upsert(self.session, DomainRank, {"domain": domain, "list_date": d},
                              {"rank": int(r["rank"]), "snapshot_id": f.snapshot_id}))
        return out

    @staticmethod
    def rank_as_of(rows: list[DomainRank], as_of: date) -> int | None:
        past = [r for r in rows if r.list_date <= as_of]
        return min(past, key=lambda r: (as_of - r.list_date).days).rank if past else None
