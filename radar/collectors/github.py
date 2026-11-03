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
from datetime import date, datetime, timedelta
from typing import Any

import httpx

from radar.collectors.base import HttpCollector, RateLimiter
from radar.collectors.common import parse_dt, record_discovery, text_has_any, upsert
from radar.config import sources, thesis
from radar.store.checkpoints import Checkpoints
from radar.store.models import Repo, RepoEvent, RepoTree

log = logging.getLogger(__name__)

REPO_FRAGMENT = """
fragment R on Repository {
  nameWithOwner description homepageUrl createdAt pushedAt stargazerCount forkCount isFork isArchived
  licenseInfo { spdxId }
  primaryLanguage { name }
  languages(first: 10, orderBy: {field: SIZE, direction: DESC}) { edges { size node { name } } }
  repositoryTopics(first: 20) { nodes { topic { name } } }
  readme: object(expression: "HEAD:README.md") { ... on Blob { byteSize } }
  owner { login __typename }
}
"""


HARDWARE_EXT = (".c", ".h", ".cc", ".cpp", ".hpp", ".cxx", ".cu", ".cuh", ".rs", ".v", ".sv",
                ".svh", ".vhd", ".vhdl", ".ino", ".urdf", ".xacro", ".sdf", ".kicad_pcb",
                ".kicad_sch", ".step", ".stp", ".stl", ".f90", ".m4", ".ld")
CODE_EXT = (*HARDWARE_EXT, ".py", ".js", ".ts", ".tsx", ".go", ".java", ".kt", ".swift", ".rb",
                           ".cs", ".scala", ".jl", ".m", ".r", ".lua", ".zig")


def tree_signals(tree: list[dict[str, Any]]) -> dict[str, Any]:
    """Quality and hardware signals from a git tree listing."""
    blobs = [t for t in tree if t.get("type") == "blob"]
    paths = [t["path"].lower() for t in blobs]
    code = [p for p in paths if p.endswith(CODE_EXT)]
    hw = [p for p in code if p.endswith(HARDWARE_EXT)]
    readme = max((t.get("size", 0) for t in blobs
                  if t["path"].lower().split("/")[-1].startswith("readme") and "/" not in t["path"]),
                 default=0)
    return {
        "has_tests": any(p.startswith(("test/", "tests/")) or "/tests/" in p or "/test/" in p
                         or p.split("/")[-1].startswith("test_") for p in paths),
        "has_ci": any(p.startswith((".github/workflows/", ".gitlab-ci", ".circleci/")) for p in paths),
        "has_license": any(p.split("/")[-1].startswith(("license", "copying")) for p in paths),
        "has_ros_package": any(p.endswith("package.xml") for p in paths),
        "readme_bytes": int(readme),
        "code_files": len(code),
        "hardware_files": len(hw) + sum(1 for p in paths if p.endswith("package.xml")),
        "file_count": len(paths),
    }


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

    def __init__(self, *args: Any, token: str | None = None, wait_on_budget: bool = False, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.wait_on_budget = wait_on_budget  # long batch jobs sleep through limits instead of stopping
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
            if not self.wait_on_budget:
                raise BudgetExhausted(f"self-imposed budget of {self.hourly_budget}/hour reached")
            pause = max(0.0, 3600 - (time.monotonic() - self._window_start))
            log.warning("hourly budget reached; sleeping %.0fs", pause)
            time.sleep(pause)
            self._window_start, self._window_count = time.monotonic(), 0
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
            if not self.wait_on_budget:
                raise BudgetExhausted(f"GitHub {resource} rate limit nearly exhausted ({remaining} left)")
            reset = int(resp.headers.get("x-ratelimit-reset", "0"))
            pause = max(1.0, min(3700, reset - time.time() + 5))
            log.warning("GitHub %s limit low; sleeping %.0fs", resource, pause)
            time.sleep(pause)

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

    @staticmethod
    def base_queries(sector: str) -> list[str]:
        """Thesis-only queries. Never contains a company name (see tests/test_leakage.py)."""
        s = thesis().sectors[sector]
        return [f"topic:{t}" for t in s.github_topics] + [f'"{kw}" in:name,description' for kw in s.keywords]

    def queries_for_sector(self, sector: str, since: date, until: date | None = None) -> list[str]:
        window = f"created:{since.isoformat()}..{until.isoformat()}" if until else f"created:>={since.isoformat()}"
        return [f"{q} {window}" for q in self.base_queries(sector)]

    def discover_exhaustive(self, sector: str, since: date, until: date,
                            checkpoints: Checkpoints | None = None) -> tuple[int, list[str]]:
        """Every repository the thesis queries return for repos created in [since, until].

        Windows with more than 1,000 results (GitHub's search cap) are split in half until
        they fit, then paged to the end, so membership never depends on a popularity sort.
        Returns (repos stored, windows still truncated at 7 days or less).

        With `checkpoints`, every finished window (and every split decision) is committed with
        its rows, and windows already recorded are skipped, so an interrupted run resumes at
        the window where it stopped.
        """
        negatives = thesis().sectors[sector].negative_keywords
        stored, truncated = 0, []

        def window(base: str, start: date, end: date) -> None:
            nonlocal stored
            q = f"{base} created:{start.isoformat()}..{end.isoformat()}"
            key = f"github/{sector}/{q}"
            cp = checkpoints.get(key) if checkpoints else None
            if cp is not None:
                if cp.status == "split":
                    mid = start + (end - start) // 2
                    window(base, start, mid)
                    window(base, mid + timedelta(days=1), end)
                else:
                    stored += int((cp.result_json or {}).get("stored", 0))
                    if (cp.result_json or {}).get("truncated"):
                        truncated.append(q)
                return
            if not self.offline:
                self.search_limiter.wait()
            f = self.fetch(f"{self.api}/search/repositories", params={"q": q, "per_page": 100, "page": 1},
                           ok_statuses=(200, 422))
            total = int((f.payload or {}).get("total_count", 0)) if f.status == 200 else 0
            if total > 1000 and (end - start).days > 6:
                if checkpoints:
                    checkpoints.mark(key, "split", {"total_count": total})
                mid = start + (end - start) // 2
                window(base, start, mid)
                window(base, mid + timedelta(days=1), end)
                return
            if total > 1000:
                truncated.append(q)
            before = stored
            pages = [(f.payload or {}).get("items", [])]
            for page in range(2, min(10, -(-total // 100)) + 1):
                if not self.offline:
                    self.search_limiter.wait()
                fp = self.fetch(f"{self.api}/search/repositories", params={"q": q, "per_page": 100, "page": page},
                                ok_statuses=(200, 422))
                pages.append((fp.payload or {}).get("items", []) if fp.status == 200 else [])
            for items in pages:
                for item in items:
                    text = f"{item.get('full_name', '')} {item.get('description') or ''}"
                    if item.get("fork") or text_has_any(text, negatives):
                        continue
                    repo = self.upsert_repo(item, f.snapshot_id)
                    record_discovery(self.session, "repos", repo.id, sector, base)
                    stored += 1
            if checkpoints:
                checkpoints.mark(key, "done", {"stored": stored - before, "total_count": total,
                                               "truncated": total > 1000})

        for base in self.base_queries(sector):
            window(base, since, until)
            self.session.flush()
        return stored, truncated

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
                record_discovery(self.session, "repos", repo.id, sector, q)
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

    def tree_at(self, full_name: str, sha: str, committed_at: datetime) -> RepoTree | None:
        """File tree at one commit. Pick the last commit on or before T for point-in-time
        quality and hardware-share features."""
        f = self.fetch(f"{self.api}/repos/{full_name}/git/trees/{sha}", params={"recursive": 1},
                       ok_statuses=(200, 404, 409, 422))
        if f.status != 200 or not f.payload:
            return None
        signals = tree_signals(f.payload.get("tree", []))
        signals["truncated"] = bool(f.payload.get("truncated"))
        return upsert(self.session, RepoTree, {"repo_full_name": full_name, "sha": sha},
                      {"committed_at": committed_at, "signals_json": signals,
                       "snapshot_id": f.snapshot_id})

    def languages(self, full_name: str) -> tuple[dict[str, int], int]:
        f = self.fetch(f"{self.api}/repos/{full_name}/languages", ok_statuses=(200, 404))
        return (f.payload if f.status == 200 else {}) or {}, f.snapshot_id
