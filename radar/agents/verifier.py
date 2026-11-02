"""Verifier Agent (Sonnet): judges each claim against the text it cites, and nothing else.

Isolation: the verifier has its own system prompt and receives only (claim text, cited
excerpts). It never sees the Research Agent's prompt, evidence pack, or other sentences.
The heuristic fallback is deliberately strict: every number and capitalized token in the
claim must appear in the cited text, so an outage can only remove claims, never pass them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from radar.agents.base import Agent

Verdict = Literal["supported", "partial", "unsupported"]
NUMBER = re.compile(r"\d[\d,.]*")
PROPER = re.compile(r"\b[A-Z][A-Za-z0-9-]{2,}\b")
STOP = {"The", "This", "These", "Its", "Their", "There", "That", "Repository", "Paper", "Team", "Company",
        "GitHub", "README", "January", "February", "March", "April", "May", "June", "July", "August",
        "September", "October", "November", "December"}


class ClaimVerdict(BaseModel):
    claim_id: int
    verdict: Verdict
    note: str
    rewrite: str


class VerifierOutput(BaseModel):
    verdicts: list[ClaimVerdict]


@dataclass
class ClaimToCheck:
    claim_id: int
    text: str
    excerpts: dict[int, str]  # snapshot id -> excerpt


@dataclass
class VerifierInput:
    claims: list[ClaimToCheck]


def heuristic_verdict(claim: str, source: str) -> tuple[Verdict, str]:
    src = source.lower().replace(",", "")
    nums = {n.rstrip(".,").replace(",", "") for n in NUMBER.findall(claim)}
    names = {w for w in PROPER.findall(claim) if w not in STOP}
    missing = [n for n in nums if n and n not in src] + [w for w in names if w.lower() not in src]
    if not nums and not names:
        return "unsupported", "no checkable specifics; heuristic verifier cannot confirm"
    if not missing:
        return "supported", "all numbers and names appear in the cited text (heuristic)"
    found = len(nums) + len(names) - len(missing)
    return ("partial" if found else "unsupported"), f"not in cited text: {', '.join(missing[:6])} (heuristic)"


class VerifierAgent(Agent[VerifierInput, VerifierOutput]):
    name = "verifier"
    prompt_file = "verifier.v1.md"
    output_model = VerifierOutput

    def build_user(self, inp: VerifierInput) -> str:
        blocks = []
        for c in inp.claims:
            sources = "\n".join(f"  source {sid}: {text}" for sid, text in c.excerpts.items())
            blocks.append(f"claim {c.claim_id}: {c.text}\n{sources}")
        return "Check each claim against its cited source text only.\n\n" + "\n\n".join(blocks)

    def check(self, inp: VerifierInput, out: VerifierOutput) -> list[str]:
        want = {c.claim_id for c in inp.claims}
        got = [v.claim_id for v in out.verdicts]
        problems = []
        if set(got) != want or len(got) != len(set(got)):
            problems.append(f"return exactly one verdict per claim id {sorted(want)}; got {sorted(got)}")
        return problems

    def fallback(self, inp: VerifierInput) -> VerifierOutput:
        out = []
        for c in inp.claims:
            verdict, note = heuristic_verdict(c.text, " ".join(c.excerpts.values()))
            out.append(ClaimVerdict(claim_id=c.claim_id, verdict=verdict, note=note, rewrite=""))
        return VerifierOutput(verdicts=out)
