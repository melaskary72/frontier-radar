"""Sourcing Agent (Haiku): a cited "why now" for one ranked candidate."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

from radar.agents.base import Agent
from radar.agents.evidence import EvidenceItem

Sector = Literal["robotics", "defense", "energy", "space", "manufacturing", "semiconductors"]
SECTORS: tuple[str, ...] = ("robotics", "defense", "energy", "space", "manufacturing", "semiconductors")
SENTENCE_END = re.compile(r"[.!?](\s|$)")


class SourcingOutput(BaseModel):
    why_now: str
    sector: Sector
    confidence: float = Field(ge=0, le=1)
    evidence_ids: list[int]


@dataclass
class SourcingInput:
    name: str
    sector: str | None
    score: float
    breakdown: dict[str, float]
    evidence: list[EvidenceItem]
    top_evidence: list[dict[str, Any]]  # from explain.py, used by the fallback


class SourcingAgent(Agent[SourcingInput, SourcingOutput]):
    name = "sourcing"
    prompt_file = "sourcing.v1.md"
    output_model = SourcingOutput

    def build_user(self, inp: SourcingInput) -> str:
        groups = ", ".join(f"{g} {v:.3f}" for g, v in sorted(inp.breakdown.items(), key=lambda kv: -kv[1]))
        ev = "\n\n".join(e.render() for e in inp.evidence)
        return (f"Candidate: {inp.name}\nSector guess: {inp.sector or 'unknown'}\n"
                f"Score: {inp.score:.3f} (group contributions: {groups})\n\nEvidence:\n{ev}")

    def check(self, inp: SourcingInput, out: SourcingOutput) -> list[str]:
        problems = []
        valid = {e.id for e in inp.evidence}
        bad = [i for i in out.evidence_ids if i not in valid]
        if bad:
            problems.append(f"evidence_ids {bad} are not in the provided evidence (valid: {sorted(valid)})")
        if not out.evidence_ids:
            problems.append("evidence_ids is empty; cite the items your sentences rely on")
        if len(SENTENCE_END.findall(out.why_now.strip())) > 3:
            problems.append("why_now has more than three sentences")
        return problems

    def fallback(self, inp: SourcingInput) -> SourcingOutput:
        labels = [e["label"] for e in inp.top_evidence if e.get("label")][:2]
        ids = [e["snapshot_id"] for e in inp.top_evidence if e.get("snapshot_id")][:3]
        if not ids:
            ids = [e.id for e in inp.evidence[:1]]
        why = ("Ranked on " + "; ".join(labels) + ".") if labels else "Ranked on its score breakdown; evidence is thin."
        sector = inp.sector if inp.sector in SECTORS else "robotics"
        return SourcingOutput(why_now=why, sector=sector, confidence=0.3, evidence_ids=ids)  # type: ignore[arg-type]
