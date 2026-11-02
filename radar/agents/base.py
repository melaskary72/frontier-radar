"""Agent base: schema-validated output, one retry, heuristic fallback, spend cap, trace.

Every agent run follows the same path:

    prompt (versioned file) -> cost check -> API call with a JSON schema -> pydantic parse
    -> semantic validation -> on failure, one retry that shows the model its error
    -> on second failure, refusal, spend cap, or no API key: deterministic heuristic fallback
    -> one `agent_runs` row with model, prompt version, tokens, cost, latency, status

Output text is normalized to house style (no em dashes) before it is returned.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from radar.agents import cost
from radar.collectors.common import clean_text
from radar.config import models
from radar.store.models import AgentRun

log = logging.getLogger(__name__)

PROMPTS = Path(__file__).resolve().parent / "prompts"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
KEEP_SCHEMA_KEYS = {"type", "properties", "required", "items", "enum", "description", "additionalProperties", "anyOf"}

def strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Pydantic schema -> structured-output schema: $refs inlined, every object closed and
    fully required, validation-only keywords stripped (pydantic re-checks them)."""
    raw = model.model_json_schema()
    defs = raw.pop("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(copy.deepcopy(defs[node["$ref"].rsplit("/", 1)[-1]]))
            out = {k: walk(v) for k, v in node.items() if k in KEEP_SCHEMA_KEYS and k != "properties"}
            if "properties" in node:  # keys here are field names, not schema keywords
                out["properties"] = {name: walk(sub) for name, sub in node["properties"].items()}
            if out.get("type") == "object" and "properties" in out:
                out["additionalProperties"] = False
                out["required"] = list(out["properties"])
            return out
        if isinstance(node, list):
            return [walk(x) for x in node]
        return node

    return walk(raw)


def load_prompt(filename: str) -> tuple[str, str]:
    """Prompt files start with a `version: <name>.v<N>` line, then a blank line, then text."""
    text = (PROMPTS / filename).read_text()
    first, _, body = text.partition("\n")
    if not first.startswith("version:"):
        raise ValueError(f"{filename} must start with a version line")
    return first.split(":", 1)[1].strip(), body.strip()


def _clean(obj: Any) -> Any:
    if isinstance(obj, str):
        return clean_text(obj) if chr(0x2014) in obj else obj
    if isinstance(obj, list):
        return [_clean(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    return obj


def default_client() -> Any | None:
    """An Anthropic client when a key is configured and we are not offline."""
    if os.environ.get("RADAR_OFFLINE") == "1" or not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    import anthropic

    return anthropic.Anthropic(max_retries=3)


@dataclass
class AgentResult[Out: BaseModel]:
    output: Out
    status: str          # ok | retried | fallback | capped
    run_id: int
    cost_usd: float
    model: str


class Agent[In, Out: BaseModel]:
    name: ClassVar[str]
    prompt_file: ClassVar[str]
    output_model: ClassVar[type[BaseModel]]

    def __init__(self, session: Session, client: Any | None = "default"):
        self.session = session
        self.client = default_client() if client == "default" else client
        role = models()["roles"][self.name]
        self.model: str = role["model"]
        self.effort: str = role["effort"]
        self.max_tokens: int = int(role["max_tokens"])
        self.prompt_version, self.system = load_prompt(self.prompt_file)

    # ---- subclass hooks -----------------------------------------------------------
    def build_user(self, inp: In) -> str:
        raise NotImplementedError

    def check(self, inp: In, out: Out) -> list[str]:
        """Semantic checks beyond the schema. Return error strings; empty means valid."""
        return []

    def fallback(self, inp: In) -> Out:
        raise NotImplementedError

    # ---- the run loop -------------------------------------------------------------
    def run(self, inp: In) -> AgentResult[Out]:
        user = self.build_user(inp)
        input_hash = hashlib.sha256(f"{self.prompt_version}\n{user}".encode()).hexdigest()
        t0 = time.monotonic()
        tokens_in = tokens_out = 0
        spent = 0.0
        served_by = self.model
        error: str | None = None
        status = "fallback"
        output: Out | None = None

        if self.client is None:
            error = "no API client (offline or no key)"
        else:
            messages: list[dict[str, Any]] = [{"role": "user", "content": user}]
            for attempt in range(2):
                try:
                    cost.check(self.session, self.model, len(self.system) + sum(len(str(m["content"])) for m in messages),
                               self.max_tokens)
                except cost.SpendCapReached as exc:
                    status, error = "capped", str(exc)
                    break
                try:
                    resp = self._call(messages)
                except Exception as exc:  # API errors after SDK retries: fall back, keep the trace
                    error = f"{type(exc).__name__}: {exc}"
                    break
                served_by = getattr(resp, "model", self.model) or self.model
                usd = cost.price_usage(served_by if served_by in models()["models"] else "claude-opus-5-5", resp.usage)
                cost.log(self.session, self.name, usd)
                spent += usd
                tokens_in += (resp.usage.input_tokens or 0) + (getattr(resp.usage, "cache_read_input_tokens", 0) or 0)
                tokens_out += resp.usage.output_tokens or 0
                if resp.stop_reason == "refusal":
                    error = f"refusal: {getattr(resp, 'stop_details', None)}"
                    break
                text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "")
                problems: list[str]
                try:
                    candidate = self.output_model.model_validate(_clean(json.loads(text)))
                    problems = self.check(inp, candidate)  # type: ignore[arg-type]
                except (ValueError, ValidationError) as exc:
                    candidate, problems = None, [str(exc)[:1500]]
                if not problems and candidate is not None:
                    output = candidate  # type: ignore[assignment]
                    status = "ok" if attempt == 0 else "retried"
                    break
                error = "; ".join(problems)[:2000]
                messages += [{"role": "assistant", "content": text or "(empty)"},
                             {"role": "user", "content": "Your output failed validation:\n" + error
                              + "\nReturn corrected JSON that fixes every issue."}]
        if output is None:
            output = self.fallback(inp)
            if status not in ("capped",):
                status = "fallback"

        run = AgentRun(agent=self.name, model=served_by if status in ("ok", "retried") else f"{self.model} (fallback)",
                       prompt_version=self.prompt_version, started_at=datetime.now(UTC),
                       latency_ms=int((time.monotonic() - t0) * 1000), input_tokens=tokens_in,
                       output_tokens=tokens_out, cost_usd=spent, status=status, input_hash=input_hash,
                       output_json=output.model_dump(mode="json"), error=error)
        self.session.add(run)
        self.session.flush()
        return AgentResult(output, status, run.id, spent, served_by)

    def _call(self, messages: list[dict[str, Any]]) -> Any:
        assert self.client is not None
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": [{"type": "text", "text": self.system, "cache_control": {"type": "ephemeral"}}],
            "messages": messages,
            "output_config": {"effort": self.effort,
                              "format": {"type": "json_schema", "schema": strict_schema(self.output_model)}},
        }
        if self.model.startswith("claude-sonnet-5-5") and models().get("sonnet_fallbacks"):
            return self.client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default", **kwargs)
        return self.client.messages.create(**kwargs)
