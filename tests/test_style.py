"""House rule: no em dashes (U+2014) anywhere in the repository's text files."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", ".venv", "node_modules", ".next", "__pycache__", ".pytest_cache",
             ".ruff_cache", ".mypy_cache", "fixtures"}
SKIP_FILES = {"Anti_Fund_Master_Brief.md"}  # private, gitignored
SUFFIXES = {".py", ".md", ".yaml", ".yml", ".toml", ".ts", ".tsx", ".js", ".mjs", ".json",
            ".css", ".html", ".txt", ".csv", ".ini", ".cfg", ".sh", ""}
EM_DASH = chr(0x2014)


def test_no_em_dashes() -> None:
    hits = []
    for p in ROOT.rglob("*"):
        if not p.is_file() or p.name in SKIP_FILES or SKIP_DIRS & set(p.relative_to(ROOT).parts):
            continue
        if p.suffix not in SUFFIXES:
            continue
        for i, line in enumerate(p.read_text(errors="ignore").splitlines(), 1):
            if EM_DASH in line:
                hits.append(f"{p.relative_to(ROOT)}:{i}")
    assert not hits, "em dashes found:\n" + "\n".join(hits)
