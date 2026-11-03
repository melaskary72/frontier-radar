"""Backtest collection: one pass over every thesis query for the union of all lookback windows.

Writes to its own database (data/backtest.db by default), never to the live radar's. Every
row it stores comes from a thesis query (see radar/collectors/*), and the query is logged in
`discoveries`, which is the audit trail run_backtest.py checks.

Resumable: every finished step (source, sector, date range) and every finished GitHub search
window is recorded in the collection_checkpoints table of the same database, committed together
with the rows it produced. A rerun skips everything already stored. Network errors are retried
per request with backoff (radar/collectors/base.py); if the network stays down longer than
that, the step is retried from its last checkpoint after a pause, up to STEP_ATTEMPTS times.

Run: uv run python eval/backtest/collect.py
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("DATABASE_URL", f"sqlite:///{ROOT / 'data' / 'backtest.db'}")
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from radar.collectors.arxiv import ArxivCollector  # noqa: E402
from radar.collectors.github import GitHubCollector  # noqa: E402
from radar.collectors.hn import HNCollector  # noqa: E402
from radar.collectors.usaspending import USAspendingCollector  # noqa: E402
from radar.config import thesis  # noqa: E402
from radar.store.checkpoints import Checkpoints  # noqa: E402
from radar.store.db import create_all, session_scope  # noqa: E402

START = date(2023, 8, 1)   # 12-month lookback before the earliest sensitivity cutoff (2024-08-14)
END = date(2026, 9, 8)     # latest cutoff: 30 days before the last announcement in rounds.csv
LEGACY_PROGRESS = ROOT / "data" / "backtest_progress.json"  # pre-checkpoint format, imported once
STEP_ATTEMPTS = 8
STEP_PAUSE_S = (60, 300, 600, 900, 1800, 1800, 1800)
log = logging.getLogger("collect")


def quarters(start: date, end: date) -> list[tuple[date, date]]:
    out, d = [], start
    while d <= end:
        nxt = date(d.year + (d.month + 2) // 12, (d.month + 2) % 12 + 1, 1)
        out.append((d, min(end, nxt - timedelta(days=1))))
        d = nxt
    return out


def years(start: date, end: date) -> list[tuple[date, date]]:
    return [(max(start, date(y, 1, 1)), min(end, date(y, 12, 31))) for y in range(start.year, end.year + 1)]


def import_legacy_progress() -> None:
    if not LEGACY_PROGRESS.exists():
        return
    keys = json.loads(LEGACY_PROGRESS.read_text())
    with session_scope() as s:
        cps = Checkpoints(s)
        for k in keys:
            if not cps.done(k):
                cps.mark(k, "done", {"imported_from": LEGACY_PROGRESS.name})
    LEGACY_PROGRESS.unlink()
    log.info("imported %d finished steps from %s", len(keys), LEGACY_PROGRESS.name)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    os.environ.setdefault("RADAR_NETWORK_PATIENCE_S", "1800")
    create_all()
    import_legacy_progress()
    with session_scope() as s:
        done = Checkpoints(s).keys()
    log.info("resuming with %d finished steps and windows on record", len(done))

    def step(key: str, fn) -> None:  # type: ignore[no-untyped-def]
        if key in done:
            return
        for attempt in range(1, STEP_ATTEMPTS + 1):
            t0 = time.monotonic()
            try:
                with session_scope() as s:
                    result = fn(s)
                    Checkpoints(s).mark(key, "done", {"result": str(result)})
            except httpx.TransportError as exc:
                if attempt == STEP_ATTEMPTS:
                    raise
                pause = STEP_PAUSE_S[min(attempt, len(STEP_PAUSE_S)) - 1]
                log.warning("%s: network still down after retries (%s); resuming from last checkpoint "
                            "in %ds (step attempt %d of %d)", key, exc, pause, attempt + 1, STEP_ATTEMPTS)
                time.sleep(pause)
                continue
            done.add(key)
            log.info("%s: %s (%.0fs)", key, result, time.monotonic() - t0)
            return

    for sector in thesis().sectors:
        step(f"github/{sector}", lambda s, sec=sector: GitHubCollector(s, wait_on_budget=True)
             .discover_exhaustive(sec, START, END, checkpoints=Checkpoints(s)))
        for a, b in quarters(START, END):
            step(f"hn/{sector}/{a}", lambda s, sec=sector, a=a, b=b: len(HNCollector(s).discover(sec, a, b, max_pages=10)))
            step(f"arxiv/{sector}/{a}", lambda s, sec=sector, a=a, b=b: ArxivCollector(s).discover_with_code(sec, a, b))
        for a, b in years(START, END):
            step(f"usaspending/{sector}/{a.year}",
                 lambda s, sec=sector, a=a, b=b: len(USAspendingCollector(s).discover(sec, a, b, max_pages=10)))
    log.info("collection complete")


if __name__ == "__main__":
    main()
