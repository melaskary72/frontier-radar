"""Hard LLM spend cap, enforced before every call, with a running `cost_log`.

Before a call: estimate the worst case (input tokens from character count at 3 characters
per token, plus the full `max_tokens` of output) and refuse the call if it could push total
spend over the cap. After a call: price the actual usage and append to cost_log.

Spend is also appended to a ledger file (data/cost_ledger.jsonl by default) that survives
database rebuilds; the cap uses the larger of the two totals, so rebuilding the database
can never reset the budget.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from radar.config import REPO_ROOT, models
from radar.store.models import CostLog


class SpendCapReached(RuntimeError):
    pass


def price(model: str, input_tokens: int, output_tokens: int, cache_read: int = 0, cache_write: int = 0) -> float:
    p = models()["models"][model]
    in_rate, out_rate = p["input"], p["output"]
    total_in = input_tokens + cache_read + cache_write
    if "long_context_threshold" in p and total_in > p["long_context_threshold"]:
        in_rate, out_rate = p["long_input"], p["long_output"]
    usd = (input_tokens * in_rate + output_tokens * out_rate
           + cache_read * p.get("cache_read", in_rate) + cache_write * p.get("cache_write", in_rate * 1.25))
    return usd / 1_000_000


def price_usage(model: str, usage: Any) -> float:
    return price(model, getattr(usage, "input_tokens", 0) or 0, getattr(usage, "output_tokens", 0) or 0,
                 getattr(usage, "cache_read_input_tokens", 0) or 0,
                 getattr(usage, "cache_creation_input_tokens", 0) or 0)


def ledger_path() -> Path:
    return Path(os.environ.get("RADAR_COST_LEDGER", REPO_ROOT / "data" / "cost_ledger.jsonl"))


def ledger_total() -> float:
    p = ledger_path()
    if not p.exists():
        return 0.0
    return sum(float(json.loads(line)["usd"]) for line in p.read_text().splitlines() if line.strip())


def spent(session: Session) -> float:
    db = float(session.scalar(select(func.coalesce(func.sum(CostLog.usd), 0.0))) or 0.0)
    return max(db, ledger_total())


def cap() -> float:
    env = os.environ.get("LLM_SPEND_CAP_USD")
    return float(env) if env else float(models()["spend_cap_usd"])


def check(session: Session, model: str, prompt_chars: int, max_tokens: int) -> float:
    """Raise SpendCapReached if the worst case of this call could exceed the cap."""
    worst = price(model, prompt_chars // 3 + 1, max_tokens)
    if spent(session) + worst > cap():
        raise SpendCapReached(f"spent ${spent(session):.4f}; worst case ${worst:.4f} would pass the ${cap():.2f} cap")
    return worst


def log(session: Session, agent: str, usd: float) -> CostLog:
    now = datetime.now(UTC)
    row = CostLog(at=now, agent=agent, usd=usd, cumulative_usd=spent(session) + usd)
    session.add(row)
    session.flush()
    p = ledger_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as f:
        f.write(json.dumps({"at": now.isoformat(), "agent": agent, "usd": usd}) + "\n")
    return row
