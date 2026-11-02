"""OpenAlex: dated affiliation history per author, for the "left a top lab" signal.

Each authorship in OpenAlex carries the institutions listed on that paper and the paper's
publication date, which gives a point-in-time affiliation timeline.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

from radar.collectors.base import HttpCollector
from radar.collectors.common import parse_date
from radar.config import sources


class OpenAlexCollector(HttpCollector):
    source = "openalex"

    @property
    def api(self) -> str:
        return sources()["endpoints"]["openalex"]

    def work_by_arxiv(self, arxiv_id: str) -> tuple[dict[str, Any] | None, int]:
        doi = f"https://doi.org/10.48550/arXiv.{arxiv_id}"
        f = self.fetch(f"{self.api}/works/{doi}", ok_statuses=(200, 404))
        return (f.payload if f.status == 200 else None), f.snapshot_id

    def author_works(self, author_id: str, until: date | None = None, per_page: int = 100
                     ) -> tuple[list[dict[str, Any]], int]:
        flt = f"author.id:{author_id}"
        if until:
            flt += f",to_publication_date:{until.isoformat()}"
        f = self.fetch(f"{self.api}/works", params={
            "filter": flt, "per-page": per_page, "sort": "publication_date:desc",
            "select": "id,publication_date,authorships",
        })
        return (f.payload or {}).get("results", []), f.snapshot_id

    @staticmethod
    def affiliation_timeline(works: list[dict[str, Any]], author_id: str) -> list[tuple[date, str]]:
        """(publication date, institution name) pairs for one author, oldest first."""
        short = author_id.rsplit("/", 1)[-1]
        out: list[tuple[date, str]] = []
        for w in works:
            d = parse_date(w.get("publication_date"))
            if d is None:
                continue
            for a in w.get("authorships", []):
                if ((a.get("author") or {}).get("id") or "").rsplit("/", 1)[-1] != short:
                    continue
                for inst in a.get("institutions", []):
                    if inst.get("display_name"):
                        out.append((d, inst["display_name"]))
        return sorted(out)

    @staticmethod
    def left_top_lab(timeline: list[tuple[date, str]], top_labs: list[str], as_of: date,
                     months: int = 18) -> str | None:
        """Return the lab name if the author's last top-lab paper is followed by papers under
        other affiliations only, and the first of those falls within `months` of as_of.

        Uses only papers published on or before as_of, so it is safe for backtests.
        """
        pattern = re.compile(r"\b(" + "|".join(re.escape(lab) for lab in top_labs) + r")\b", re.I)

        def is_top(name: str) -> bool:
            return bool(pattern.search(name))

        past = [(d, i) for d, i in timeline if d <= as_of]
        tops = [(d, i) for d, i in past if is_top(i)]
        if not tops:
            return None
        last_top_date, lab = tops[-1]
        after = [d for d, _ in past if d > last_top_date]
        if not after:
            return None  # still there, or no evidence of a move
        horizon = as_of - timedelta(days=int(months * 30.4))
        return lab if after[0] >= horizon else None
