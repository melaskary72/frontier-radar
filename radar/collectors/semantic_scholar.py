"""Semantic Scholar Graph API: author citation count, h-index, paper count, co-authors.

Semantic Scholar exposes only an author's current affiliations, so affiliation history
(the "left a top lab" signal) comes from OpenAlex authorships; see openalex.py.
"""

from __future__ import annotations

import os
from typing import Any

from radar.collectors.base import HttpCollector
from radar.config import sources

AUTHOR_FIELDS = "name,affiliations,citationCount,hIndex,paperCount,externalIds"
PAPER_FIELDS = "title,year,publicationDate,externalIds,authors"


class SemanticScholarCollector(HttpCollector):
    source = "semantic_scholar"

    def headers(self) -> dict[str, str]:
        key = os.environ.get("S2_API_KEY", "").strip()
        return {"x-api-key": key} if key else {}

    @property
    def api(self) -> str:
        return sources()["endpoints"]["semantic_scholar"]

    def paper_by_arxiv(self, arxiv_id: str) -> tuple[dict[str, Any] | None, int]:
        f = self.fetch(f"{self.api}/paper/arXiv:{arxiv_id}",
                       params={"fields": f"{PAPER_FIELDS},authors.authorId"}, ok_statuses=(200, 404))
        return (f.payload if f.status == 200 else None), f.snapshot_id

    def author(self, author_id: str) -> tuple[dict[str, Any] | None, int]:
        f = self.fetch(f"{self.api}/author/{author_id}", params={"fields": AUTHOR_FIELDS},
                       ok_statuses=(200, 404))
        return (f.payload if f.status == 200 else None), f.snapshot_id

    def authors_batch(self, ids: list[str]) -> tuple[list[dict[str, Any] | None], int]:
        """Up to 1,000 authors per call; one request instead of N."""
        f = self.fetch(f"{self.api}/author/batch", method="POST", params={"fields": AUTHOR_FIELDS},
                       json_body={"ids": ids[:1000]}, ok_statuses=(200, 400))
        return (f.payload if f.status == 200 else []), f.snapshot_id

    def author_papers(self, author_id: str, limit: int = 100) -> tuple[list[dict[str, Any]], int]:
        f = self.fetch(f"{self.api}/author/{author_id}/papers",
                       params={"fields": "title,year,publicationDate,authors", "limit": limit},
                       ok_statuses=(200, 404))
        data = (f.payload or {}).get("data", []) if f.status == 200 else []
        return data, f.snapshot_id

    @staticmethod
    def coauthor_counts(papers: list[dict[str, Any]], self_id: str) -> dict[str, int]:
        """How often each co-author appears: repeat collaborators are a founder signal."""
        counts: dict[str, int] = {}
        for p in papers:
            for a in p.get("authors", []):
                aid = a.get("authorId")
                if aid and aid != self_id:
                    counts[aid] = counts.get(aid, 0) + 1
        return counts
