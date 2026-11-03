"""Live Picks: today's top frontier companies with no Form D yet, as a dated, falsifiable bet.

Selection, in rank order from the latest scored run:
- a company, not an individual: an organization, launch, or federal awardee (individual GitHub
  accounts are skipped unless they carry a company domain);
- not a university group or large company, judged from the GitHub profile (classify_profile);
- no SEC Form D on file: exact normalized name or GitHub organization, distinctive names only,
  against every Form D filed since 2024-01-01 (data/cache/formd/filings.csv, built by
  eval/backtest/formd_fetch.py). Older filings are not checked, and the output says so.

Each pick carries the radar's why-now line (the Sourcing Agent writes one if the scored run did
not), its score and rank, and the resolution rule: on the resolution date, a pick counts as a hit
if a new Form D was filed under the same matcher, or a priced round was publicly reported.
"""

from __future__ import annotations

import csv
import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from radar.collectors.common import clean_model_prose, clean_text
from radar.collectors.github import GitHubCollector
from radar.collectors.web import WebCollector
from radar.config import REPO_ROOT
from radar.entities.resolve import classify_profile, distinctive, normalize_domain, strict_name
from radar.store.models import Company, Score, WebPage

log = logging.getLogger(__name__)

FORMD_INDEX = REPO_ROOT / "data" / "cache" / "formd" / "filings.csv"
OUT_DIR = REPO_ROOT / "picks"
HORIZON_DAYS = 182
RESOLUTION_RULE = ("Hit if, by the resolution date, the company files a new SEC Form D (exact normalized name "
                   "or GitHub organization, distinctive names only) or a priced round is publicly reported. "
                   "Miss otherwise. Graded once, on the resolution date, and the grade is published either way.")


@dataclass
class Pick:
    n: int
    company_id: int
    name: str
    sector: str | None
    github_org: str | None
    domain: str | None
    radar_rank: int
    score: float
    why_now: str
    why_now_source: str
    top_evidence: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)  # for a person to rule on, never auto-dropped
    label: str = ""          # a person's label with its source (picks/labels.csv), e.g. YC-backed
    label_source: str = ""


def formd_names(path: Path = FORMD_INDEX) -> tuple[set[str], str | None]:
    """Normalized distinctive issuer names with any Form D filing, and the latest filing date."""
    names: set[str] = set()
    latest: str | None = None
    if not path.exists():
        return names, None
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            if distinctive(r["entity_name"]) and (k := strict_name(r["entity_name"])):
                names.add(k)
            latest = max(latest or r["filed_on"], r["filed_on"])
    return names, latest


def company_keys(c: Company) -> list[str]:
    keys = []
    for s in (c.name, (c.github_org or "").replace("-", " ").replace("_", " ")):
        if s and distinctive(s) and (k := strict_name(s)):
            keys.append(k)
    return keys


LAB_PHRASES = re.compile(r"[^.|]{0,80}\b(university|universit[a-z]+|laboratory|research team|research group|"
                         r"lab at|institute of|phd students?|professor)\b[^.|]{0,80}", re.I)


def site_flags(session: Session, domain: str | None) -> list[str]:
    """Phrases on the home or About page that suggest a research group rather than a company.
    Startups also say "spun out of a university", so these are flags for review, not filters."""
    if not domain:
        return []
    pages = session.scalars(select(WebPage).where(WebPage.domain == domain,
                                                  WebPage.page_kind.in_(("home", "about", "team")))).all()
    out = []
    for pg in pages:
        m = LAB_PHRASES.search(pg.text)
        if m:
            out.append(f"possible research group ({pg.page_kind} page): \"{clean_text(m.group(0).strip())}\"")
    return out[:2]


EXCLUSIONS = OUT_DIR / "exclusions.csv"
EVIDENCE_REF = re.compile(r"\s*\((?:evidence|source|sources|ids?)\b[^)]*\)", re.I)


LABELS = OUT_DIR / "labels.csv"


def load_labels(path: Path = LABELS) -> dict[str, tuple[str, str]]:
    """Labels a person attached, each with a source URL (picks/labels.csv)."""
    if not path.exists():
        return {}
    with open(path, newline="") as f:
        return {r["github_org"].lower(): (r["label"], r["source_url"]) for r in csv.DictReader(f) if r.get("github_org")}


def load_exclusions(path: Path = EXCLUSIONS) -> set[str]:
    """Companies a person ruled out (picks/exclusions.csv: github_org, name, reason, decided_by)."""
    if not path.exists():
        return set()
    with open(path, newline="") as f:
        return {v.lower() for r in csv.DictReader(f) for v in (r.get("github_org"), r.get("name")) if v}


def has_live_site(session: Session, domain: str) -> bool:
    return session.scalar(select(WebPage.id).where(WebPage.domain == domain.lower().removeprefix("www."),
                                                   WebPage.page_kind == "home")) is not None


def clean_why_now(text: str) -> str:
    """Strip evidence references the Sourcing Agent sometimes leaks into prose ("(evidence 1, 2021)")."""
    return clean_model_prose(EVIDENCE_REF.sub("", text)).strip()


def _is_company(c: Company) -> bool:
    return c.kind in ("org", "launch", "awardee") or bool(c.domain)


def select_picks(session: Session, as_of: date, n: int = 10, *, gh: GitHubCollector | None = None,
                 web: WebCollector | None = None, formd: set[str] | None = None,
                 why_now: Any = None) -> tuple[list[Pick], dict[str, int]]:
    """`why_now(company, score_row) -> str` writes a line when the scored run has none."""
    rows = list(session.execute(select(Score, Company).join(Company, Company.id == Score.company_id)
                                .where(Score.as_of == as_of).order_by(Score.rank)))
    formd = formd if formd is not None else formd_names()[0]
    skipped = {"individual": 0, "lab_or_incumbent": 0, "has_form_d": 0, "no_company_website": 0, "excluded": 0}
    excluded = load_exclusions()
    labels = load_labels()
    picks: list[Pick] = []
    for sc, c in rows:
        if len(picks) >= n:
            break
        if (c.github_org or "").lower() in excluded or c.name.lower() in excluded:
            skipped["excluded"] += 1
            continue
        if c.kind in ("lab", "incumbent", "stale"):
            skipped["lab_or_incumbent"] += 1
            continue
        if not _is_company(c):
            skipped["individual"] += 1
            continue
        prof = None
        if gh is not None and c.github_org:
            prof, _ = gh.profile(c.github_org, c.kind)
            if tag := classify_profile(c.github_org, prof):
                c.kind = tag
                skipped["lab_or_incumbent"] += 1
                continue
        if any(k in formd for k in company_keys(c)):
            skipped["has_form_d"] += 1
            continue
        # Company evidence: a live company website we fetched (robots.txt obeyed). The domain
        # comes from the company record, else from the GitHub profile's website field.
        domain = c.domain or normalize_domain(str((prof or {}).get("blog") or "")) or None
        if web is not None and domain:
            web.collect_site(domain)
        if not domain or not has_live_site(session, domain):
            skipped["no_company_website"] += 1
            continue
        bd = sc.breakdown_json or {}
        line, src = str(bd.get("why_now") or ""), "scored run (Sourcing Agent)"
        if not line and why_now is not None:
            line, src = why_now(c, sc), "written for this list (Sourcing Agent)"
        top = [str(e.get("label", ""))[:140] for e in (bd.get("top_evidence") or [])[:3]]
        label, label_src = labels.get((c.github_org or "").lower(), ("", ""))
        picks.append(Pick(len(picks) + 1, c.id, c.name, c.sector, c.github_org, domain, sc.rank,
                          round(sc.total, 3), clean_why_now(line), src, top, site_flags(session, domain),
                          label, label_src))
    return picks, skipped


def write_picks(picks: list[Pick], as_of: date, skipped: dict[str, int], formd_latest: str | None,
                out_dir: Path = OUT_DIR) -> tuple[Path, Path]:
    resolve_on = as_of + timedelta(days=HORIZON_DAYS)
    out_dir.mkdir(parents=True, exist_ok=True)
    meta = {"as_of": as_of.isoformat(), "resolve_on": resolve_on.isoformat(), "rule": RESOLUTION_RULE,
            "form_d_checked_through": formd_latest, "form_d_checked_from": "2024-01-01", "skipped": skipped,
            "picks": [asdict(p) for p in picks]}
    jp = out_dir / f"{as_of.isoformat()}.json"
    jp.write_text(json.dumps(meta, indent=1))
    lines = [f"# Live Picks, {as_of.isoformat()}", "",
             f"Today's top {len(picks)} frontier companies on the radar with no SEC Form D on file. "
             f"Resolution date: {resolve_on.isoformat()}.", "", f"Rule: {RESOLUTION_RULE}", "",
             f"Form D checked from 2024-01-01 through {formd_latest}. Skipped on the way down the ranking: "
             f"{skipped['lab_or_incumbent']} labs or large companies, {skipped['individual']} individual accounts, "
             f"{skipped['has_form_d']} with a Form D, {skipped['no_company_website']} with no live company website, "
             f"{skipped['excluded']} excluded by a person (picks/exclusions.csv).", "",
             "| # | Company | Sector | Radar rank | Why now | Flags |", "|---|---|---|---|---|---|"]
    for p in picks:
        ident = p.domain or (f"github.com/{p.github_org}" if p.github_org else "")
        tag = f"**{p.label}** ([source]({p.label_source})). " if p.label else ""
        lines.append(f"| {p.n} | {p.name} ({ident}) | {p.sector or ''} | {p.radar_rank} | {tag}{p.why_now} | "
                     f"{'; '.join(p.flags) or 'none'} |")
    mp = out_dir / f"{as_of.isoformat()}.md"
    mp.write_text("\n".join(lines) + "\n")
    return jp, mp
