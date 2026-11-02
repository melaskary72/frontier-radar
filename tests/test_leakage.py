"""Leakage guard: portfolio company names and domains may not appear in radar/ or config/.

The backtest is only honest if the system that produces rankings has no way to look a
portfolio company up by name. This test runs in CI on every push.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SCANNED = [ROOT / "radar", ROOT / "config"]
TEXT_SUFFIXES = {".py", ".yaml", ".yml", ".md", ".json", ".txt", ".sql", ".toml"}


def load_terms() -> tuple[list[dict], list[dict]]:
    data = yaml.safe_load((ROOT / "eval" / "leakage_terms.yaml").read_text())
    return data["companies"], data.get("allowlist", [])


def strip_block(text: str, block: str) -> str:
    """Remove a top-level YAML block (its key line and indented or list lines)."""
    out, skipping = [], False
    for line in text.splitlines():
        if line.startswith(f"{block}:"):
            skipping = True
            continue
        if skipping and (line.startswith((" ", "-", "#")) or not line.strip()):
            continue
        skipping = False
        out.append(line)
    return "\n".join(out)


def patterns(company: dict) -> list[re.Pattern[str]]:
    pats = []
    if company["mode"] in ("word", "phrase"):
        pats.append(re.compile(rf"(?<![\w-]){re.escape(company['name'])}(?![\w-])", re.I))
    for d in company.get("domains") or []:
        pats.append(re.compile(re.escape(d), re.I))
    return pats


def scanned_files() -> list[Path]:
    files = []
    for base in SCANNED:
        files += [p for p in base.rglob("*") if p.is_file() and p.suffix in TEXT_SUFFIXES
                  and "__pycache__" not in p.parts]
    return files


def test_terms_file_is_complete() -> None:
    companies, _ = load_terms()
    backtest = [c for c in companies if c.get("backtest")]
    assert len(backtest) == 12, "primary (8) and secondary (4) sets from Part 5.11"
    for c in companies:
        if c["mode"] == "domain":
            assert "domains" in c, f"{c['name']} is domain-matched and needs a domains list"


def test_no_portfolio_names_outside_eval() -> None:
    companies, allowlist = load_terms()
    hits = []
    for path in scanned_files():
        rel = path.relative_to(ROOT).as_posix()
        text = path.read_text(errors="ignore")
        allowed: set[str] = set()
        stripped = text
        for a in allowlist:
            if a["file"] == rel:
                allowed |= set(a["names"])
                stripped = strip_block(text, a["block"])
        for c in companies:
            body = stripped if c["name"] in allowed else text
            for pat in patterns(c):
                for m in pat.finditer(body):
                    line = body[: m.start()].count("\n") + 1
                    hits.append(f"{rel}:{line}: {m.group(0)!r} ({c['name']})")
    assert not hits, "portfolio names leaked into scored code or config:\n" + "\n".join(hits)


def test_guard_catches_a_planted_name(tmp_path: Path) -> None:
    companies, _ = load_terms()
    planted = "QUERY = 'topic:robotics Efference'"
    assert any(p.search(planted) for c in companies for p in patterns(c))
    assert not any(p.search("def merge(a, b): return natural_sort(a + b)")
                   for c in companies for p in patterns(c))
