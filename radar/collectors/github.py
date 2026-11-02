"""GitHub collector: repository discovery, stargazer timelines, commits, contributors, owners.

Point-in-time design: momentum features come from timestamped events (forks, issues, pull
requests, releases, commits), never from today's counts, so a backtest at date T sees only
activity that existed at T.

Finding (November 2026): GitHub's REST stargazers endpoint returns 404 and the GraphQL
stargazers connection returns zero edges for repositories the token does not own, while
still reporting the total count. Historical starred_at timelines are therefore not
observable through the public API. Recent stars (90 days) remain visible as WatchEvents in
the Events API and feed the live radar only.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from datetime import date
from typing import Any

import httpx

from radar.collectors.base import HttpCollector, RateLimiter
from radar.collectors.common import parse_dt, text_has_any, upsert
from radar.config import sources, thesis
from radar.store.models import Repo, RepoEvent

log = logging.getLogger(__name__)

REPO_FRAGMENT = """
fragment R on Repository {
  nameWithOwner createdAt pushedAt stargazerCount forkCount isFork isArchived
  licenseInfo { spdxId }
  primaryLanguage { name }
  languages(first: 10, orderBy: {field: SIZE, direction: DESC}) { edges { size node { name } } }
  repositoryTopics(first: 20) { nodes { topic { name } } }
  readme: object(expression: "HEAD:README.md") { ... on Blob { byteSize } }
  owner { login __typename }
}
"""


class BudgetExhausted(RuntimeError):
    pass


def github_token() -> str | None:
    tok = os.environ.get("GITHUB_TOKEN", "").strip()
    if tok:
        return tok
    try:  # Local convenience: reuse the GitHub CLI login. CI always sets GITHUB_TOKEN.
        out = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


class GitHubCollector(HttpCollector):
    source = "github"

    def __init__(self, *args: Any, token: str | None = None, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.token = token if token is not None else (None if self.offline else github_token())
        cfg = sources()
        self.api = cfg["endpoints"]["github_rest"]
        self.gql = cfg["endpoints"]["github_graphql"]
        search_interval = float(cfg["rate_limits"]["github_search"])
        if self.min_interval_override is not None:
            search_interval = self.min_interval_override
        self.search_limiter = RateLimiter(search_interval)
        self.hourly_budget = int(cfg["rate_limits"]["github_hourly_budget"])
        self._window_start = time.monotonic()
        self._window_count = 0

    def headers(self) -> dict[str, str]:
        h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def after_response(self, resp: httpx.Response) -> None:
        if time.monotonic() - self._window_start > 3600:
            self._window_start, self._window_count = time.monotonic(), 0
        self._window_count += 1
        if self._window_count >= self.hourly_budget:
            raise BudgetExhausted(f"self-imposed budget of {self.hourly_budget}/hour reached")
        remaining = resp.headers.get("x-ratelimit-remaining")
        if remaining is None or not remaining.isdigit():
            return
        resource = resp.headers.get("x-ratelimit-resource", "core")
        if resource == "search":
            # 30 per minute; the search limiter paces us, so only sleep if GitHub says zero.
            if int(remaining) == 0:
                reset = int(resp.headers.get("x-ratelimit-reset", "0"))
                time.sleep(max(0, min(61, reset - time.time() + 1)))
        elif int(remaining) < 50:
            raise BudgetExhausted(f"GitHub {resource} rate limit nearly exhausted ({remaining} left)")

    # ------------------------------------------------------------------ discovery

    def search_repos(
        self, q: str, *, sort: str = "stars", max_pages: int = 2, per_page: int = 50
    ) -> list[tuple[dict[str, Any], int]]:
        """Run one search query. Returns (repo json, snapshot id) pairs."""
        out: list[tuple[dict[str, Any], int]] = []
        for page in range(1, max_pages + 1):
            if not self.offline:
                self.search_limiter.wait()
            f = self.fetch(
                f"{self.api}/search/repositories",
                params={"q": q, "sort": sort, "order": "desc", "per_page": per_page, "page": page},
                ok_statuses=(200, 422),
            )
            if f.status != 200 or not f.payload:
                break
            items = f.payload.get("items", [])
            out.extend((it, f.snapshot_id) for it in items)
            if len(items) < per_page:
                break
        return out

    def queries_for_sector(self, sector: str, since: date, until: date | None = None) -> list[str]:
        """Thesis-only queries. Never contains a company name (see tests/test_leakage.py)."""
        s = thesis().sectors[sector]
        window = f"created:{since.isoformat()}..{until.isoformat()}" if until else f"created:>={since.isoformat()}"
        qs = [f"topic:{t} {window}" for t in s.github_topics]
        qs += [f'"{kw}" in:name,description {window}' for kw in s.keywords]
        return qs

    def discover(
        self, sector: str, since: date, until: date | None = None, max_pages: int = 1
    ) -> list[Repo]:
        negatives = thesis().sectors[sector].negative_keywords
        seen: dict[str, Repo] = {}
        for q in self.queries_for_sector(sector, since, until):
            for item, snap_id in self.search_repos(q, max_pages=max_pages):
                text = f"{item.get('full_name', '')} {item.get('description') or ''}"
                if item.get("fork") or text_has_any(text, negatives):
                    continue
                repo = self.upsert_repo(item, snap_id)
                seen[repo.full_name] = repo
        return list(seen.values())

    def upsert_repo(self, item: dict[str, Any], snapshot_id: int) -> Repo:
        created = parse_dt(item.get("created_at"))
        assert created is not None
        return upsert(
            self.session,
            Repo,
            {"full_name": item["full_name"]},
            {
                "owner": item["owner"]["login"],
                "owner_type": item["owner"].get("type"),
                "description": item.get("description"),
                "language": item.get("language"),
                "created_at": created,
                "pushed_at": parse_dt(item.get("pushed_at")),
                "stars": item.get("stargazers_count", 0),
                "forks": item.get("forks_count", 0),
                "topics_json": item.get("topics", []),
                "homepage": item.get("homepage") or None,
                "snapshot_id": snapshot_id,
            },
        )

    # ------------------------------------------------------------------ timelines
    #
    # Every method below stores RepoEvent rows with GitHub's own historical timestamps, so
    # a backtest at date T counts only events that existed at T.

    def _event(self, repo: str, kind: str, ext_id: str, actor: str | None, at: str | None,
               snap: int, is_external: bool | None = None) -> int:
        ts = parse_dt(at)
        if ts is None:
            return 0
        upsert(self.session, RepoEvent,
               {"repo_full_name": repo, "kind": kind, "external_id": ext_id},
               {"actor": actor, "at": ts, "snapshot_id": snap, "is_external": is_external})
        return 1

    def _pages(self, url: str, params: dict[str, Any], max_pages: int) -> list[tuple[list[Any], int]]:
        out = []
        for page in range(1, max_pages + 1):
            f = self.fetch(url, params={**params, "per_page": 100, "page": page},
                           ok_statuses=(200, 404, 409, 451))
            if f.status != 200 or not isinstance(f.payload, list) or not f.payload:
                break
            out.append((f.payload, f.snapshot_id))
            if len(f.payload) < 100:
                break
        return out

    def forks(self, full_name: str, max_pages: int = 5) -> int:
        """Fork timeline, oldest first: the point-in-time stand-in for star velocity."""
        n = 0
        for items, snap in self._pages(f"{self.api}/repos/{full_name}/forks", {"sort": "oldest"}, max_pages):
            for fk in items:
                n += self._event(full_name, "fork", str(fk["id"]), fk["owner"]["login"],
                                 fk.get("created_at"), snap, True)
        return n

    def issues(self, full_name: str, max_pages: int = 5) -> int:
        """Issues and pull requests, oldest first. Outside authors signal real adoption."""
        n = 0
        internal = {"OWNER", "MEMBER", "COLLABORATOR"}
        url = f"{self.api}/repos/{full_name}/issues"
        for items, snap in self._pages(url, {"state": "all", "sort": "created", "direction": "asc"}, max_pages):
            for it in items:
                kind = "pr" if "pull_request" in it else "issue"
                n += self._event(full_name, kind, str(it["id"]), (it.get("user") or {}).get("login"),
                                 it.get("created_at"), snap, it.get("author_association") not in internal)
        return n

    def releases(self, full_name: str, max_pages: int = 2) -> int:
        n = 0
        for items, snap in self._pages(f"{self.api}/repos/{full_name}/releases", {}, max_pages):
            for r in items:
                n += self._event(full_name, "release", str(r["id"]), (r.get("author") or {}).get("login"),
                                 r.get("published_at") or r.get("created_at"), snap, False)
        return n

    def recent_stars(self, full_name: str, max_pages: int = 3) -> int:
        """WatchEvents from the public Events API (last 90 days, at most 300 events).

        Live radar only. The backtest never reads kind="star" (see eval/backtest).
        """
        n = 0
        for items, snap in self._pages(f"{self.api}/repos/{full_name}/events", {}, max_pages):
            for ev in items:
                if ev.get("type") == "WatchEvent":
                    n += self._event(full_name, "star", str(ev["id"]), (ev.get("actor") or {}).get("login"),
                                     ev.get("created_at"), snap, True)
        return n

    def commits(self, full_name: str, since: date | None = None, until: date | None = None,
                max_pages: int = 3) -> int:
        params = {"since": f"{since.isoformat()}T00:00:00Z" if since else None,
                  "until": f"{until.isoformat()}T23:59:59Z" if until else None}
        n, last = 0, None
        for items, snap in self._pages(f"{self.api}/repos/{full_name}/commits", params, max_pages):
            for c in items:
                at = ((c.get("commit") or {}).get("author") or {}).get("date")
                n += self._event(full_name, "commit", c["sha"], (c.get("author") or {}).get("login"),
                                 at, snap, None)
                ts = parse_dt(at)
                if ts and (last is None or ts > last):
                    last = ts
        if last is not None:
            repo = self.session.query(Repo).filter_by(full_name=full_name).first()
            if repo and (repo.last_commit_at is None or repo.last_commit_at < last):
                repo.last_commit_at = last
        return n

    def repo_batch(self, full_names: list[str]) -> tuple[dict[str, dict[str, Any] | None], int]:
        """GraphQL: metadata for up to 50 repositories in one request."""
        parts = []
        for i, fn in enumerate(full_names[:50]):
            owner, name = fn.split("/", 1)
            parts.append(f'r{i}: repository(owner: {json.dumps(owner)}, name: {json.dumps(name)}) {{ ...R }}')
        query = "query {\n" + "\n".join(parts) + "\n}\n" + REPO_FRAGMENT
        f = self.fetch(self.gql, method="POST", json_body={"query": query})
        data = (f.payload or {}).get("data") or {}
        return {fn: data.get(f"r{i}") for i, fn in enumerate(full_names[:50])}, f.snapshot_id

    def contributors(self, full_name: str) -> list[dict[str, Any]]:
        f = self.fetch(f"{self.api}/repos/{full_name}/contributors",
                       params={"per_page": 100}, ok_statuses=(200, 204, 404))
        people = f.payload if f.status == 200 and isinstance(f.payload, list) else []
        repo = self.session.query(Repo).filter_by(full_name=full_name).first()
        if repo:
            repo.contributors = len(people)
        return people

    def owner(self, login: str) -> tuple[dict[str, Any] | None, int]:
        """User or org profile: company, bio, blog, location, created_at."""
        f = self.fetch(f"{self.api}/users/{login}", ok_statuses=(200, 404))
        return (f.payload if f.status == 200 else None), f.snapshot_id

    def repo_tree_signals(self, full_name: str, default_branch: str = "HEAD") -> dict[str, Any]:
        """Repo quality inputs: tests present, CI present, license, README length."""
        f = self.fetch(f"{self.api}/repos/{full_name}/git/trees/{default_branch}",
                       params={"recursive": 1}, ok_statuses=(200, 404, 409))
        paths = [t["path"] for t in (f.payload or {}).get("tree", [])] if f.status == 200 else []
        low = [p.lower() for p in paths]
        return {
            "snapshot_id": f.snapshot_id,
            "has_tests": any(p.startswith(("test", "tests/")) or "/test" in p for p in low),
            "has_ci": any(p.startswith((".github/workflows/", ".gitlab-ci", ".circleci/")) for p in low),
            "has_license": any(p.split("/")[-1].startswith(("license", "copying")) for p in low),
            "has_ros_package": any(p.endswith("package.xml") for p in low),
            "file_count": len(paths),
        }

    def languages(self, full_name: str) -> tuple[dict[str, int], int]:
        f = self.fetch(f"{self.api}/repos/{full_name}/languages", ok_statuses=(200, 404))
        return (f.payload if f.status == 200 else {}) or {}, f.snapshot_id
