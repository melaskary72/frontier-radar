"""Public-footprint audit for the backtest targets (eval only, never touches the radar DB).

For each company in rounds.csv: does it have a GitHub account whose blog points at its
domain, Hacker News stories linking its domain, or federal awards under its name, and when
did each first appear? This sets the ceiling on what any public-data radar could have seen,
and is what the backtest report's coverage discussion rests on.

Name lookups are allowed here because this file lives in eval/ and its output never feeds
ranking. Run: uv run python eval/backtest/footprint.py
"""

from __future__ import annotations

import csv
import json
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
OUT = HERE / "footprints.csv"


def gh_token() -> str:
    return subprocess.run(["gh", "auth", "token"], capture_output=True, text=True).stdout.strip()


def github_accounts(client: httpx.Client, name: str, domain: str) -> list[dict]:
    """Accounts whose login matches the name or whose blog field contains the domain."""
    found = {}
    stem = domain.split(".")[0]
    for q in (f"{stem} in:login", f"{name} in:name", f"{stem} type:org"):
        r = client.get("https://api.github.com/search/users", params={"q": q, "per_page": 10})
        time.sleep(2.2)
        for it in r.json().get("items", []):
            u = client.get(it["url"]).json()
            blog = (u.get("blog") or "").lower()
            if domain in blog or u.get("login", "").lower() in (stem, name.lower().replace(" ", ""), name.lower().replace(" ", "-")):
                found[u["login"]] = {"login": u["login"], "type": u.get("type"), "blog": u.get("blog"),
                                     "created_at": u.get("created_at"), "public_repos": u.get("public_repos"),
                                     "blog_matches_domain": domain in blog}
    return list(found.values())


def first_repo_date(client: httpx.Client, login: str) -> str | None:
    r = client.get(f"https://api.github.com/users/{login}/repos", params={"per_page": 100, "sort": "created", "direction": "asc"})
    repos = [x for x in r.json() if isinstance(x, dict) and not x.get("fork")] if r.status_code == 200 else []
    return repos[0]["created_at"] if repos else None


def hn_links(client: httpx.Client, domain: str) -> list[dict]:
    r = client.get("https://hn.algolia.com/api/v1/search", params={
        "query": domain, "restrictSearchableAttributes": "url", "tags": "story", "hitsPerPage": 50})
    hits = [h for h in r.json().get("hits", []) if domain in (h.get("url") or "")]
    return sorted(({"title": h["title"], "points": h.get("points"), "created_at": h["created_at"]} for h in hits),
                  key=lambda h: h["created_at"])


def awards(client: httpx.Client, name: str) -> list[dict]:
    body = {"filters": {"recipient_search_text": [name], "award_type_codes": ["A", "B", "C", "D", "02", "03", "04", "05"],
                        "time_period": [{"start_date": "2018-01-01", "end_date": "2026-10-31"}]},
            "fields": ["Recipient Name", "Award Amount", "Start Date", "Awarding Agency", "Description"],
            "limit": 20, "page": 1, "sort": "Start Date", "order": "asc"}
    r = client.post("https://api.usaspending.gov/api/v2/search/spending_by_award/", json=body)
    return [x for x in r.json().get("results", []) if name.lower() in (x.get("Recipient Name") or "").lower()]


def main() -> None:
    with open(HERE / "rounds.csv", newline="") as f:
        rows = list(csv.DictReader(f))
    gh = httpx.Client(headers={"Authorization": f"Bearer {gh_token()}", "Accept": "application/vnd.github+json"}, timeout=30)
    plain = httpx.Client(timeout=30, headers={"User-Agent": "FrontierRadar eval footprint audit"})
    out = []
    for r in rows:
        name, domain = r["company"], r["domain"]
        accts = github_accounts(gh, name, domain)
        for a in accts:
            a["first_repo_at"] = first_repo_date(gh, a["login"])
        stories = hn_links(plain, domain)
        aw = awards(plain, name if name != "Aeon" else "Aeon Industrial")
        out.append({
            "company": name, "domain": domain, "announced_on": r["announced_on"],
            "github_accounts": json.dumps(accts), "github_domain_match": any(a["blog_matches_domain"] for a in accts),
            "hn_stories": len(stories), "hn_first": stories[0]["created_at"][:10] if stories else "",
            "hn_max_points": max((s["points"] or 0 for s in stories), default=0),
            "federal_awards": len(aw), "award_first": aw[0]["Start Date"] if aw else "",
            "checked_at": datetime.now(UTC).isoformat(timespec="seconds"),
        })
        print(f"{name:18} gh={[(a['login'], a['blog_matches_domain'], (a['first_repo_at'] or '')[:10]) for a in accts]} "
              f"hn={len(stories)} first={out[-1]['hn_first']} awards={len(aw)}")
    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0]))
        w.writeheader()
        w.writerows(out)


if __name__ == "__main__":
    main()
