"""Hacker News via the Algolia API: Show HN and Launch HN posts that match thesis terms."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, time
from typing import Any

from radar.collectors.base import HttpCollector
from radar.collectors.common import record_discovery, text_has_any, upsert
from radar.config import sources, thesis
from radar.store.models import HNPost


def _epoch(d: date) -> int:
    return int(datetime.combine(d, time.min, tzinfo=UTC).timestamp())


def title_pattern(term: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![\w-]){re.escape(term)}s?(?![\w-])", re.I)


def classify(title: str) -> str | None:
    t = title.lower()
    if t.startswith("show hn"):
        return "show_hn"
    if t.startswith("launch hn"):
        return "launch_hn"
    return None


class HNCollector(HttpCollector):
    source = "hn"

    def search(self, query: str, since: date, until: date | None = None, max_pages: int = 2
               ) -> list[tuple[dict[str, Any], int]]:
        url = f"{sources()['endpoints']['hn']}/search_by_date"
        numeric = f"created_at_i>={_epoch(since)}"
        if until:
            numeric += f",created_at_i<{_epoch(until)}"
        out: list[tuple[dict[str, Any], int]] = []
        for page in range(max_pages):
            f = self.fetch(url, params={
                "query": query, "tags": "story", "numericFilters": numeric, "hitsPerPage": 100,
                "page": page, "restrictSearchableAttributes": "title", "typoTolerance": "false",
            })
            hits = (f.payload or {}).get("hits", [])
            out.extend((h, f.snapshot_id) for h in hits)
            if page + 1 >= (f.payload or {}).get("nbPages", 0):
                break
        return out

    def discover(self, sector: str, since: date, until: date | None = None, max_pages: int = 2) -> list[HNPost]:
        posts: dict[str, HNPost] = {}
        s = thesis().sectors[sector]
        for term in s.title_terms or s.keywords:
            # Algolia matches loosely; keep titles with the term as a whole word (or plural).
            pattern = title_pattern(term)
            for h, snap in self.search(term, since, until, max_pages=max_pages):
                title = h.get("title") or ""
                kind = classify(title)
                if kind is None or not pattern.search(title) or text_has_any(title, s.negative_keywords):
                    continue
                post = self.upsert_post(h, kind, snap)
                record_discovery(self.session, "hn_posts", post.id, sector, f"hn:{term}")
                posts[h["objectID"]] = post
        return list(posts.values())

    def upsert_post(self, h: dict[str, Any], kind: str, snap: int) -> HNPost:
        return upsert(self.session, HNPost, {"object_id": h["objectID"]}, {
            "kind": kind, "title": h.get("title") or "", "url": h.get("url"),
            "points": h.get("points") or 0, "num_comments": h.get("num_comments") or 0,
            "author": h.get("author"),
            "created_at": datetime.fromtimestamp(h["created_at_i"], tz=UTC),
            "snapshot_id": snap,
        })
