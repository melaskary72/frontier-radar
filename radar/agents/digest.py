"""Digest Agent (Haiku): Monday email and a two-minute audio script."""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel

from radar.agents.base import Agent


class DigestOutput(BaseModel):
    subject: str
    email_markdown: str
    audio_script: str


@dataclass
class DigestCandidate:
    name: str
    sector: str
    score: float
    why_now: str


@dataclass
class DigestInput:
    week_of: str
    top_new: list[DigestCandidate]
    movers: list[tuple[str, int, int]]  # name, last week's rank, this week's rank
    verifier_supported_rate: float
    claims_checked: int
    spend_week_usd: float
    spend_total_usd: float
    notes: list[str] = field(default_factory=list)


class DigestAgent(Agent[DigestInput, DigestOutput]):
    name = "digest"
    prompt_file = "digest.v1.md"
    output_model = DigestOutput

    def build_user(self, inp: DigestInput) -> str:
        top = "\n".join(f"{i}. {c.name} ({c.sector}, score {c.score:.2f}): {c.why_now}"
                        for i, c in enumerate(inp.top_new, 1))
        movers = "\n".join(f"- {n}: rank {a} to {b}" for n, a, b in inp.movers) or "- none"
        return (f"Week of {inp.week_of}\n\nTop new candidates:\n{top}\n\nMovers:\n{movers}\n\n"
                f"Verifier: {inp.verifier_supported_rate:.0%} of {inp.claims_checked} checked claims supported.\n"
                f"Agent spend: ${inp.spend_week_usd:.2f} this week, ${inp.spend_total_usd:.2f} to date.")

    def check(self, inp: DigestInput, out: DigestOutput) -> list[str]:
        words = len(out.audio_script.split())
        problems = []
        if not 200 <= words <= 360:
            problems.append(f"audio_script is {words} words; write 240 to 320")
        if len(out.subject) > 90:
            problems.append("subject is too long")
        return problems

    def fallback(self, inp: DigestInput) -> DigestOutput:
        lead = inp.top_new[0] if inp.top_new else None
        email = [f"Frontier Radar, week of {inp.week_of}.", ""]
        email += [f"{i}. **{c.name}** ({c.sector}, {c.score:.2f}): {c.why_now}" for i, c in enumerate(inp.top_new, 1)]
        email += ["", "Movers:"] + [f"- {n}: {a} to {b}" for n, a, b in inp.movers]
        email += ["", f"Verifier: {inp.verifier_supported_rate:.0%} of {inp.claims_checked} claims supported. "
                      f"Spend: ${inp.spend_week_usd:.2f} this week."]
        spoken = [f"Good morning. This is Frontier Radar for the week of {inp.week_of}."]
        for c in inp.top_new[:5]:
            spoken.append(f"{c.name}, in {c.sector}, scored {c.score:.2f}. {c.why_now}")
        spoken.append(f"The verifier supported {inp.verifier_supported_rate:.0%} of {inp.claims_checked} claims this week.")
        return DigestOutput(subject=f"Frontier Radar: {lead.name} leads this week" if lead else "Frontier Radar weekly",
                            email_markdown="\n".join(email), audio_script=" ".join(spoken))
