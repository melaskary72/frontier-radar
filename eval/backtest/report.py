"""Render docs/backtest-report.md and docs/assets/backtest-timeline.svg from logged results.

Every number comes from eval/backtest/results.json, rounds.csv, footprints.csv, or
eval/gates.json. The written analysis lives in eval/backtest/analysis.md and is included
verbatim, so prose and numbers are reviewed separately.

Run: uv run python eval/backtest/report.py
"""

from __future__ import annotations

import csv
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
DOC = ROOT / "docs" / "backtest-report.md"
SVG = ROOT / "docs" / "assets" / "backtest-timeline.svg"
FOOTER = ("Frontier Radar is an independent work sample by Mohamed El Askary, built for the Anti Fund MTS / "
          "Associate application. Not affiliated with Anti Fund.")


def pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.1%}"


def stage(r: dict[str, Any]) -> str:
    if r["rank"]:
        return f"rank {r['rank']}"
    for key, label in (("enriched", "enriched, not ranked"), ("passed_thesis", "passed thesis gate"),
                       ("in_universe", "in universe, failed thesis gate"), ("ever_collected", "collected, outside window")):
        if r[key]:
            return label
    return "not found"


def load() -> tuple[dict[str, Any], list[dict[str, str]], dict[str, dict[str, str]], dict[str, Any]]:
    results = json.loads((HERE / "results.json").read_text())
    with open(HERE / "rounds.csv", newline="") as f:
        rounds = list(csv.DictReader(f))
    foot: dict[str, dict[str, str]] = {}
    if (HERE / "footprints.csv").exists():
        with open(HERE / "footprints.csv", newline="") as f:
            foot = {r["company"]: r for r in csv.DictReader(f)}
    gates_path = ROOT / "eval" / "gates.json"
    gates = json.loads(gates_path.read_text())["gates"] if gates_path.exists() else {}
    return results, rounds, foot, gates


def footprint_events(company: str, foot: dict[str, dict[str, str]]) -> list[tuple[date, str]]:
    r = foot.get(company)
    if not r:
        return []
    ev = []
    for a in json.loads(r["github_accounts"]):
        if a.get("blog_matches_domain") and a.get("first_repo_at"):
            ev.append((date.fromisoformat(a["first_repo_at"][:10]), f"first public repo ({a['login']})"))
    if r["hn_first"]:
        ev.append((date.fromisoformat(r["hn_first"]), "first Hacker News link to domain"))
    if r["award_first"]:
        ev.append((date.fromisoformat(r["award_first"][:10]), "first federal award"))
    return ev


def timeline_svg(rounds: list[dict[str, str]], foot: dict[str, dict[str, str]]) -> str:
    start, end = date(2024, 7, 1), date(2026, 11, 1)
    w, left, row_h, top = 860, 150, 30, 40
    h = top + row_h * len(rounds) + 40
    span = (end - start).days

    def x(d: date) -> float:
        return left + (w - left - 20) * max(0, min(span, (d - start).days)) / span

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
           'font-family="Inter, Arial, sans-serif" font-size="12">',
           '<rect width="100%" height="100%" fill="#FAF8F3"/>',
           '<text x="10" y="22" font-size="14" fill="#16181A">Public footprint, cutoffs, and announcement per company</text>']
    for yr in (2025, 2026):
        xx = x(date(yr, 1, 1))
        out.append(f'<line x1="{xx:.0f}" y1="{top - 8}" x2="{xx:.0f}" y2="{h - 30}" stroke="#D8DEE0"/>'
                   f'<text x="{xx + 3:.0f}" y="{h - 16}" fill="#5B6166">{yr}</text>')
    for i, r in enumerate(rounds):
        y = top + i * row_h + 14
        t = date.fromisoformat(r["announced_on"])
        muted = r["included"] != "yes"
        color = "#5B6166" if muted else "#16181A"
        out.append(f'<text x="10" y="{y + 4}" fill="{color}">{r["company"]}{" *" if muted else ""}</text>')
        out.append(f'<line x1="{left}" y1="{y}" x2="{w - 20}" y2="{y}" stroke="#E8E4DA"/>')
        out.append(f'<rect x="{x(date.fromordinal(t.toordinal() - 90)):.0f}" y="{y - 5}" '
                   f'width="{x(t) - x(date.fromordinal(t.toordinal() - 90)):.0f}" height="10" fill="#0F5257" opacity="0.15"/>')
        for d, label in footprint_events(r["company"], foot):
            out.append(f'<circle cx="{x(d):.0f}" cy="{y}" r="5" fill="#A8874B"><title>{label}: {d}</title></circle>')
        out.append(f'<path d="M{x(t):.0f} {y - 7} l6 7 l-6 7 l-6 -7 z" fill="#0F5257"><title>announced {t}</title></path>')
    ly = h - 16
    out.append(f'<rect x="{left + 120}" y="{ly - 9}" width="18" height="10" fill="#0F5257" opacity="0.15"/>'
               f'<text x="{left + 142}" y="{ly}" fill="#5B6166">90 days before announcement</text>'
               f'<circle cx="{left + 345}" cy="{ly - 4}" r="5" fill="#A8874B"/>'
               f'<text x="{left + 355}" y="{ly}" fill="#5B6166">public footprint</text>'
               f'<path d="M{left + 470} {ly - 11} l6 7 l-6 7 l-6 -7 z" fill="#0F5257"/>'
               f'<text x="{left + 482}" y="{ly}" fill="#5B6166">announcement (* low-confidence date)</text>')
    out.append("</svg>")
    return "\n".join(out)


def formd_section() -> list[str]:
    path = HERE / "formd_results.json"
    if not path.exists():
        return []
    fd = json.loads(path.read_text())
    out = ["## Test 1: did the radar's top 100 go on to raise?", "",
           f"Outcome: a new Form D (not an amendment) filed within 6 or 12 months after the cutoff. Only cutoffs "
           f"whose window had fully passed by {fd['data_end']} count. Matching: {fd['matching']}. Intervals are 95% "
           "bootstrap intervals that resample companies, because a company recurs across cutoffs.", "",
           "| Window | Cutoffs | Radar top 100 | Stars top 100 | In-thesis universe | Raw lift | Lift vs same entity type | Companies with a hit |",
           "|---|---|---|---|---|---|---|---|"]
    for h, r in fd["horizons"].items():
        a, std = r["all"], r["type_standardized"]["radar"]
        def rate(arm: str, a: dict = a) -> str:
            return f"{a[arm]['rate'] * 100:.2f}% ({a[arm]['hits']} of {a[arm]['observations']:,})"
        lift = a["lift_radar_vs_random"]
        out.append(f"| {h} | {len(r['eligible_cutoffs'])} | {rate('radar')} | {rate('stars')} | "
                   f"{a['random']['rate'] * 100:.3f}% ({a['random']['hits']} of {a['random']['observations']:,}) | "
                   f"{lift['lift']:.1f}x ({lift['ci95'][0]:.2f} to {lift['ci95'][1]:.2f}) | "
                   f"{std['lift']:.2f}x ({std['ci95'][0]:.2f} to {std['ci95'][1]:.2f}) | {std['companies_with_a_hit']} |")
    out += ["", "Sensitivities (excluding companies with any Form D before the cutoff; only matchable names) are in "
            "`eval/backtest/formd_results.json`. Every matched filing is listed in `eval/backtest/formd_matches.csv`.", ""]
    return out


def main() -> None:
    results, rounds, foot, gates = load()
    hl, sec, sens = results["headline"], results["secondary"], results["sensitivity_low_confidence_dates"]
    rows = results["rows"]
    lines = ["# Backtest Report", "",
             f"_Generated {results['generated']} from `eval/backtest/results.json`. Every number below is computed; "
             "the analysis section is written by hand._", "",
             *formd_section(),
             "## Test 2: Anti Fund's portfolio", "",
             "Using only data available before each round was publicly announced, would Frontier Radar have "
             "ranked Anti Fund's early-stage frontier investments highly?", "",
             "| Set | Companies | Offset | In universe | Passed thesis gate | Recall@25 | Recall@100 | Stars baseline recall@100 | Random recall@100 | Median candidates |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for label, s in (("Primary (frontier)", hl), ("Secondary (applied AI)", sec), ("Sensitivity (low-confidence dates)", sens)):
        for o in results["protocol"]["offsets_days"]:
            m = s[f"t_minus_{o}"]
            lines.append(f"| {label} | {s['n_companies']} | T minus {o} | {m['coverage_in_universe']} | {m['passed_thesis']} | "
                         f"{pct(m['recall_at_25'])} | {pct(m['recall_at_100'])} | {pct(m['baseline_recall_at_100'])} | "
                         f"{pct(m['random_expected_recall_at_100'])} | {m['median_in_thesis']:,.0f} |")
    lead = hl["median_lead_days_top100"]
    lines += ["", f"Primary companies found in the top 100 at any cutoff: {hl['companies_found_top100']} of {hl['n_companies']}. "
              f"Median lead time: {'n/a' if lead is None else f'{lead} days'}.", ""]

    lines += ["![Timeline](assets/backtest-timeline.svg)", "", "## Per company", "",
              "| Company | Set | Announced | Date confidence | Verified public footprint before T | T minus 90 | T minus 30 | Stars baseline (T minus 30) |",
              "|---|---|---|---|---|---|---|---|"]
    by = {(r["company"], r["offset_days"]): r for r in rows}
    for r in rounds:
        t = date.fromisoformat(r["announced_on"])
        ev = [f"{label} {d}" for d, label in footprint_events(r["company"], foot) if d < t]
        r90, r30 = by[(r["company"], 90)], by[(r["company"], 30)]
        lines.append(f"| {r['company']} | {r['set']} | {r['announced_on']} | {r['date_confidence']} | "
                     f"{'; '.join(ev) if ev else 'none found'} | {stage(r90)} | {stage(r30)} | "
                     f"{r30['baseline_rank'] or 'not in universe'} |")

    qa = results["query_audit"]
    lines += ["", "## Leakage checks", "",
              f"- Query audit: {qa['distinct_queries']} distinct discovery queries; queries naming a portfolio company: "
              f"{len(qa['queries_naming_a_target'])}; companies with no discovery row: {qa['companies_without_discovery_row']}. "
              f"Result: {'pass' if qa['passed'] else 'FAIL'}.",
              "- `tests/test_leakage.py` fails CI if any portfolio name or domain appears in `radar/` or `config/`.",
              "- Features read only rows timestamped on or before the cutoff; thesis text and prescore breadth are "
              "cut at the cutoff too. Features with no history (job boards, current citations, recent stars) are "
              "excluded in backtest mode.", ""]

    p = results["protocol"]
    lines += ["## Protocol", "",
              f"- Cutoffs at T minus {p['offsets_days'][0]} and T minus {p['offsets_days'][1]} days, T = public announcement (sources in `eval/backtest/rounds.csv`).",
              f"- Universe: entities first seen within {p['lookback_days']} days before the cutoff, from thesis queries only "
              "(GitHub search windows paged exhaustively, Hacker News launches, code-linked arXiv papers, USAspending awards).",
              f"- Prescore with hindsight-free signals, enrich the top {p['enrich_top']}, score the top {p['score_top']}.",
              f"- Baseline: {p['baseline']}.",
              "- Rows with low-confidence dates are reported separately and excluded from the headline.",
              f"- Runtime: {results['runtime_seconds'] / 60:.0f} minutes.", ""]

    if gates:
        lines += ["## Eval gates at the time of this report", "", "| Gate | Value | Target | Status |", "|---|---|---|---|"]
        for k, g in gates.items():
            lines.append(f"| {k.replace('_', ' ')} | {g['value']} | {g['target']} | {g['status']} |")
        lines.append("")

    analysis = HERE / "analysis.md"
    lines += [analysis.read_text().strip() if analysis.exists() else "## Analysis\n\n_Pending review._", "", "---", "", FOOTER, ""]
    DOC.write_text("\n".join(lines))
    SVG.parent.mkdir(parents=True, exist_ok=True)
    SVG.write_text(timeline_svg(rounds, foot))
    print(f"wrote {DOC} and {SVG} at {datetime.now():%H:%M}")


if __name__ == "__main__":
    main()
