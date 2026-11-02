"""SEC EDGAR Form D: private offerings. A Form D is the "consensus arrived" marker.

Two paths:
1. Name lookup through EDGAR full-text search (forms=D) to answer "has this discovered
   company filed a Form D, and when?"
2. Daily form index plus each filing's primary_doc.xml for discovery by industry group.

SEC fair-access policy: a declared User-Agent with contact details and at most 10 requests
per second. Set SEC_USER_AGENT in .env.
"""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from datetime import date
from typing import Any

from radar.collectors.base import HttpCollector
from radar.collectors.common import parse_date, upsert
from radar.config import sources
from radar.store.models import Filing


def _num(v: str | None) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except ValueError:
        return None  # "Indefinite"


def parse_form_d_xml(text: str) -> dict[str, Any]:
    root = ET.fromstring(text)

    def find(path: str) -> str | None:
        el = root.find(path)
        return el.text.strip() if el is not None and el.text else None

    return {
        "company_name": find("./primaryIssuer/entityName"),
        "cik": find("./primaryIssuer/cik"),
        "state": find("./primaryIssuer/issuerAddress/stateOrCountry"),
        "industry": find("./offeringData/industryGroup/industryGroupType"),
        "total_offering": _num(find("./offeringData/offeringSalesAmounts/totalOfferingAmount")),
        "amount_sold": _num(find("./offeringData/offeringSalesAmounts/totalAmountSold")),
    }


INDEX_LINE = re.compile(r"^(D|D/A)\s{2,}(.+?)\s{2,}(\d+)\s+(\d{8})\s+(edgar/data/\S+)$")


def parse_daily_index(text: str) -> list[dict[str, str]]:
    """Parse form.YYYYMMDD.idx: fixed-width rows of form type, company, CIK, date, path."""
    out = []
    for line in text.splitlines():
        m = INDEX_LINE.match(line.strip())
        if m:
            out.append({"form": m.group(1), "company": m.group(2).strip(), "cik": m.group(3),
                        "date": m.group(4), "path": m.group(5)})
    return out


class EdgarCollector(HttpCollector):
    source = "edgar"

    def headers(self) -> dict[str, str]:
        ua = os.environ.get("SEC_USER_AGENT", "").strip()
        return {"User-Agent": ua} if ua else {}

    def search_form_d(self, name: str, start: date | None = None, end: date | None = None
                      ) -> list[dict[str, Any]]:
        """Form D hits for an exact company-name phrase. Returns filing metadata dicts."""
        params: dict[str, Any] = {"q": f'"{name}"', "forms": "D"}
        if start and end:
            params.update({"dateRange": "custom", "startdt": start.isoformat(), "enddt": end.isoformat()})
        f = self.fetch(sources()["endpoints"]["edgar_fts"], params=params, ok_statuses=(200, 404))
        hits = ((f.payload or {}).get("hits") or {}).get("hits", []) if f.status == 200 else []
        out = []
        for h in hits:
            src = h.get("_source", {})
            adsh = src.get("adsh") or h.get("_id", "").split(":")[0]
            ciks = src.get("ciks") or [""]
            out.append({
                "accession": adsh, "cik": ciks[0].lstrip("0"),
                "company_name": (src.get("display_names") or [name])[0],
                "form_type": src.get("form") or src.get("file_type") or "D",
                "filed_at": src.get("file_date"), "snapshot_id": f.snapshot_id,
            })
        return out

    def daily_index(self, d: date) -> tuple[list[dict[str, str]], int]:
        q = (d.month - 1) // 3 + 1
        url = f"{sources()['endpoints']['edgar_archives']}/daily-index/{d.year}/QTR{q}/form.{d:%Y%m%d}.idx"
        f = self.fetch(url, parse="text", ok_statuses=(200, 403, 404))
        if f.status != 200:
            return [], f.snapshot_id
        return parse_daily_index(f.payload["text"]), f.snapshot_id

    def primary_doc(self, cik: str, accession: str) -> tuple[dict[str, Any] | None, int, str]:
        acc = accession.replace("-", "")
        url = f"{sources()['endpoints']['edgar_archives']}/data/{cik}/{acc}/primary_doc.xml"
        f = self.fetch(url, parse="text", ok_statuses=(200, 404))
        if f.status != 200:
            return None, f.snapshot_id, url
        return parse_form_d_xml(f.payload["text"]), f.snapshot_id, url

    def store_filing(self, meta: dict[str, Any], detail: dict[str, Any] | None, url: str,
                     snapshot_id: int) -> Filing:
        filed = parse_date(meta.get("filed_at") or meta.get("date"))
        assert filed is not None
        d = detail or {}
        return upsert(self.session, Filing, {"accession": meta["accession"]}, {
            "cik": str(meta.get("cik") or d.get("cik") or "").lstrip("0"),
            "company_name": d.get("company_name") or meta.get("company_name") or meta.get("company"),
            "form_type": meta.get("form_type") or meta.get("form") or "D",
            "filed_at": filed, "industry": d.get("industry"),
            "total_offering": d.get("total_offering"), "amount_sold": d.get("amount_sold"),
            "state": d.get("state"), "url": url, "snapshot_id": snapshot_id,
        })

    def discover_day(self, d: date, industries: list[str] | None = None, limit: int = 200) -> list[Filing]:
        """Form D filings on day d, filtered to thesis-relevant industry groups."""
        industries = industries or sources()["form_d_industry_groups"]
        rows, _ = self.daily_index(d)
        out = []
        for r in rows[:limit]:
            accession = r["path"].rsplit("/", 1)[-1].removesuffix(".txt")
            detail, snap, url = self.primary_doc(r["cik"], accession)
            if detail is None or (detail.get("industry") not in industries):
                continue
            out.append(self.store_filing({**r, "accession": accession, "filed_at": r["date"]},
                                         detail, url, snap))
        return out
