"""LP Update Draft: a quarterly-style portfolio update built by template from tracked facts.

No LLM. Every sentence is generated from one or more portfolio_facts rows and lists their
fact ids and source URLs, so nothing in the draft is unsupported. Public proxies only.

Run: DATABASE_URL=sqlite:///data/live.db uv run python portfolio/lp_update.py
Writes web/public/data/lp_update.json.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("DATABASE_URL", f"sqlite:///{ROOT / 'data' / 'live.db'}")
sys.path.insert(0, str(ROOT))

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from radar.collectors.common import clean_text  # noqa: E402
from radar.store.db import session_scope  # noqa: E402
from radar.store.models import PortfolioCompany, PortfolioFact  # noqa: E402

OUT = ROOT / "web" / "public" / "data" / "lp_update.json"
ROUND_WINDOW_DAYS = 365
LABEL = "Draft built from public proxies only. Not Anti Fund's actual marks or metrics."


def sentence(text: str, facts: list[tuple[PortfolioFact, str]]) -> dict[str, Any]:
    assert facts, "every sentence must rest on at least one fact"
    return {"text": clean_text(text),
            "citations": [{"fact_id": f.id, "company": name, "fact_type": f.fact_type, "as_of": f.as_of.isoformat(),
                           "source_url": f.source_url} for f, name in facts]}


def money(n: float | None) -> str:
    if not n:
        return ""
    return f"${n / 1e9:,.1f}B" if n >= 1e9 else f"${n / 1e6:,.1f}M".replace(".0M", "M")


def build(s: Session) -> dict[str, Any]:
    as_of = s.scalar(select(func.max(PortfolioFact.as_of)))
    if as_of is None:
        raise SystemExit("no portfolio facts; run portfolio/collect.py first")
    names = {c.id: c.name for c in s.scalars(select(PortfolioCompany))}
    facts = list(s.scalars(select(PortfolioFact).where(PortfolioFact.as_of == as_of)))
    by_type: dict[str, list[tuple[PortfolioFact, str, dict[str, Any]]]] = {}
    for f in facts:
        by_type.setdefault(f.fact_type, []).append((f, names[f.company_id], json.loads(f.value)))

    sections: dict[str, list[dict[str, Any]]] = {"highlights": [], "new_rounds": [], "hiring_momentum": [],
                                                 "risks": []}
    # New rounds: publicly reported in the last 12 months, newest first.
    cutoff = as_of - timedelta(days=ROUND_WINDOW_DAYS)
    rounds = sorted((x for x in by_type.get("press_round", [])
                     if date.fromisoformat(x[2]["announced_on"]) >= cutoff),
                    key=lambda x: x[2]["announced_on"], reverse=True)
    for f, name, v in rounds:
        amt = money(v["n"])
        sections["new_rounds"].append(sentence(
            f"{name} announced its {amt + ' ' if amt else ''}{v['round']} on {v['announced_on']}.", [(f, name)]))
    if rounds:
        total = sum(v["n"] or 0 for _, _, v in rounds)
        sections["highlights"].append(sentence(
            f"{len(rounds)} portfolio companies announced rounds in the last 12 months, "
            f"{money(total)} in total as reported in public press.", [(f, n) for f, n, _ in rounds]))

    # Hiring momentum: largest public boards, engineering share.
    roles = sorted(by_type.get("open_roles", []), key=lambda x: x[2]["n"], reverse=True)
    for f, name, v in roles[:8]:
        sections["hiring_momentum"].append(sentence(
            f"{name} lists {v['n']:,} open roles on its public board, {v['engineering']:,} of them engineering.",
            [(f, name)]))
    if roles:
        total_roles = sum(v["n"] for _, _, v in roles)
        sections["highlights"].append(sentence(
            f"Across the {len(roles)} companies with a verified public job board, {total_roles:,} roles are open.",
            [(f, n) for f, n, _ in roles]))

    # Web traction: best Tranco ranks.
    ranks = sorted(by_type.get("tranco_rank", []), key=lambda x: x[2]["n"])
    if ranks:
        top = ranks[:3]
        sections["highlights"].append(sentence(
            "Highest web traffic ranks (Tranco): " + "; ".join(f"{n} at {v['n']:,}" for _, n, v in top) + ".",
            [(f, n) for f, n, _ in top]))

    # Open-source activity.
    pushed = sorted(by_type.get("github_pushed_90d", []), key=lambda x: x[2]["n"], reverse=True)
    active = [x for x in pushed if x[2]["n"] > 0]
    if active:
        sections["highlights"].append(sentence(
            "Most active public GitHub organizations in the last 90 days: "
            + "; ".join(f"{n} ({v['n']} repositories pushed)" for _, n, v in active[:3]) + ".",
            [(f, n) for f, n, _ in active[:3]]))

    # Form D filings.
    for f, name, v in sorted(by_type.get("form_d_filing", []), key=lambda x: x[2]["filed_at"], reverse=True):
        sections["new_rounds"].append(sentence(
            f"{v['filer']} filed a Form D with the SEC (Securities and Exchange Commission) on {v['filed_at']}.",
            [(f, name)]))

    # Risks: only what a fact shows.
    for f, name, v in roles:
        if v["n"] == 0:
            sections["risks"].append(sentence(f"{name}'s public job board lists no open roles.", [(f, name)]))
    for f, name, v in pushed:
        if v["n"] == 0:
            sections["risks"].append(sentence(
                f"{name}'s public GitHub organization had no repository pushes in the last 90 days.", [(f, name)]))

    n_companies = len({f.company_id for f in facts})
    return {"title": f"Portfolio update draft, quarter ending {as_of.isoformat()}", "as_of": as_of.isoformat(),
            "label": LABEL, "companies_with_facts": n_companies, "facts_used": len(facts),
            "generator": "portfolio/lp_update.py (template, no LLM)", "sections": sections,
            "section_titles": {"highlights": "Highlights", "new_rounds": "New rounds and filings",
                               "hiring_momentum": "Hiring momentum", "risks": "Watch items"},
            "limits": ("Public proxies cannot see revenue, burn, churn, or private rounds. A section with "
                       "no sentences means no tracked fact supported one.")}


def main() -> None:
    with session_scope() as s:
        doc = build(s)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, indent=1))
    n = sum(len(v) for v in doc["sections"].values())
    print(f"wrote {OUT}: {n} sentences from {doc['facts_used']} facts")


if __name__ == "__main__":
    main()
