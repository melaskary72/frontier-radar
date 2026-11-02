"""arXiv API collector. Queries by category plus thesis keywords, ordered by submittedDate.

arXiv terms of use ask for one request every three seconds; config/sources.yaml enforces it.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

import feedparser

from radar.collectors.base import HttpCollector
from radar.collectors.common import parse_dt, text_has_any, upsert
from radar.config import sources, thesis
from radar.store.models import Paper

ID_RE = re.compile(r"arxiv\.org/abs/([^v]+)(v\d+)?$")


def build_query(category: str, keyword: str | None, since: date, until: date) -> str:
    window = f"submittedDate:[{since:%Y%m%d}0000 TO {until:%Y%m%d}2359]"
    q = f"cat:{category} AND {window}"
    if keyword:
        q += f' AND abs:"{keyword}"'
    return q


def parse_feed(text: str) -> list[dict[str, Any]]:
    feed = feedparser.parse(text)
    out = []
    for e in feed.entries:
        m = ID_RE.search(e.get("id", ""))
        if not m:
            continue
        authors = []
        for a in e.get("authors", []):
            aff = a.get("arxiv_affiliation")
            authors.append({"name": a.get("name", "").strip(), "affiliations": [aff] if aff else []})
        out.append({
            "arxiv_id": m.group(1),
            "title": " ".join(e.get("title", "").split()),
            "abstract": " ".join(e.get("summary", "").split()),
            "submitted_at": e.get("published"),
            "categories": [t["term"] for t in e.get("tags", [])],
            "authors": authors,
        })
    return out


class ArxivCollector(HttpCollector):
    source = "arxiv"

    def search(self, query: str, *, max_results: int = 100, max_pages: int = 3) -> list[tuple[dict[str, Any], int]]:
        url = sources()["endpoints"]["arxiv"]
        out: list[tuple[dict[str, Any], int]] = []
        for page in range(max_pages):
            f = self.fetch(url, params={
                "search_query": query, "start": page * max_results, "max_results": max_results,
                "sortBy": "submittedDate", "sortOrder": "descending",
            }, parse="text")
            entries = parse_feed(f.payload["text"])
            out.extend((e, f.snapshot_id) for e in entries)
            if len(entries) < max_results:
                break
        return out

    def discover(self, sector: str, since: date, until: date, max_results: int = 100) -> list[Paper]:
        s = thesis().sectors[sector]
        papers: dict[str, Paper] = {}
        for cat in s.arxiv_categories:
            # Broad categories (cs.LG, cs.CV) only count with a thesis keyword in the abstract.
            keywords: list[str | None] = list(s.keywords) if cat in s.arxiv_filtered else [None]
            for kw in keywords:
                for e, snap in self.search(build_query(cat, kw, since, until), max_results=max_results):
                    if text_has_any(f"{e['title']} {e['abstract']}", s.negative_keywords):
                        continue
                    papers[e["arxiv_id"]] = self.upsert_paper(e, snap)
        return list(papers.values())

    def upsert_paper(self, e: dict[str, Any], snapshot_id: int) -> Paper:
        submitted = parse_dt(e["submitted_at"])
        assert submitted is not None
        return upsert(self.session, Paper, {"arxiv_id": e["arxiv_id"]}, {
            "title": e["title"], "abstract": e["abstract"], "submitted_at": submitted,
            "categories_json": e["categories"], "authors_json": e["authors"],
            "snapshot_id": snapshot_id,
        })
