"""Research Agent (Sonnet) and the memo pipeline: draft, verify, publish.

    evidence pack -> Research Agent draft (structured sentences with citations)
    -> uncited facts and citations to unknown ids are dropped before verification
    -> Verifier Agent judges every cited fact against its excerpts only
    -> supported: kept; partial: replaced by the verifier's rewrite when the rewrite passes
       the strict heuristic against the same excerpts, otherwise removed; unsupported: removed
    -> Memo row (published markdown with [c:<snapshot_id>] markers) and one Claim row each
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from radar.agents.base import Agent
from radar.agents.evidence import EvidenceItem, evidence_pack
from radar.agents.verifier import ClaimToCheck, VerifierAgent, VerifierInput, heuristic_verdict
from radar.store.models import Claim, Company, Memo

log = logging.getLogger(__name__)

SECTIONS = ["Snapshot", "Team", "Technology", "Market and why now", "Signals timeline", "Risks", "Open questions"]
Heading = Literal["Snapshot", "Team", "Technology", "Market and why now", "Signals timeline", "Risks", "Open questions"]


class Sentence(BaseModel):
    text: str
    kind: Literal["fact", "analysis"]
    citations: list[int]


class Section(BaseModel):
    heading: Heading
    sentences: list[Sentence]


class MemoDraft(BaseModel):
    sections: list[Section]
    founder_call_questions: list[str]


@dataclass
class ResearchInput:
    company: str
    sector: str | None
    as_of: date
    evidence: list[EvidenceItem]


class ResearchAgent(Agent[ResearchInput, MemoDraft]):
    name = "research"
    prompt_file = "research.v1.md"
    output_model = MemoDraft

    def build_user(self, inp: ResearchInput) -> str:
        ev = "\n\n".join(e.render() for e in inp.evidence)
        return (f"Company: {inp.company}\nSector: {inp.sector or 'unknown'}\nWrite as of: {inp.as_of}\n\n"
                f"Evidence ({len(inp.evidence)} items):\n{ev}")

    def check(self, inp: ResearchInput, out: MemoDraft) -> list[str]:
        """Structure only. Citation hygiene (uncited facts, unknown ids) is enforced
        downstream in write_memo, where offending sentences are dropped and counted,
        which is cheaper than a retry and leaves an audit trail."""
        problems = []
        headings = [s.heading for s in out.sections]
        if headings != SECTIONS:
            problems.append(f"sections must be exactly, in order: {SECTIONS}; got {headings}")
        if not 8 <= len(out.founder_call_questions) <= 10:
            problems.append(f"need 8 to 10 founder_call_questions, got {len(out.founder_call_questions)}")
        return problems

    def fallback(self, inp: ResearchInput) -> MemoDraft:
        """Deterministic memo: one cited fact per evidence item, grouped by kind."""
        by_heading: dict[str, list[Sentence]] = {h: [] for h in SECTIONS}
        route = {"GitHub repository": "Snapshot", "Hacker News post": "Snapshot", "arXiv paper": "Technology",
                 "GitHub file tree": "Technology", "OpenAlex affiliations": "Team", "Federal award": "Signals timeline",
                 "SEC Form D": "Signals timeline"}
        for e in inp.evidence:
            heading = route.get(e.kind, "Signals timeline")
            by_heading[heading].append(Sentence(text=f"{e.kind}: {e.excerpt[:220]}", kind="fact", citations=[e.id]))
        by_heading["Risks"].append(Sentence(text="Automated draft without model review; risks not assessed.",
                                            kind="analysis", citations=[]))
        by_heading["Open questions"].append(Sentence(text="Team, funding, and customers are not established by public evidence.",
                                                     kind="analysis", citations=[]))
        qs = ["What is the core technical bottleneck you are solving, and how do you measure progress on it?",
              "Who is on the founding team, and what did each person build before?",
              "What does the system do today that it could not do six months ago?",
              "Who is the first paying customer, and what do they pay for?",
              "What hardware, data, or facilities does the next milestone require?",
              "Which incumbent or lab is closest to your approach, and why do you win?",
              "What would make this a venture-scale company rather than a research project?",
              "How much capital gets you to the next proof point, and what is that proof point?"]
        return MemoDraft(sections=[Section(heading=h, sentences=by_heading[h]) for h in SECTIONS],  # type: ignore[arg-type]
                         founder_call_questions=qs)


@dataclass
class PublishedMemo:
    memo: Memo
    claims: list[Claim]
    summary: dict[str, int | float | str]


def write_memo(session: Session, company: Company, as_of: date, *, client: object = "default") -> PublishedMemo:
    evidence = evidence_pack(session, company, as_of)
    excerpts = {e.id: f"{e.title} (fetched {e.fetched_at}): {e.excerpt}" for e in evidence}
    research = ResearchAgent(session, client=client)
    draft = research.run(ResearchInput(company.name, company.sector, as_of, evidence))

    # Number every cited fact; drop uncited facts and unknown citations before verification.
    to_check: list[ClaimToCheck] = []
    pre_removed = 0
    for s in draft.output.sections:
        for sent in s.sentences:
            if sent.kind != "fact":
                continue
            cites = [c for c in sent.citations if c in excerpts]
            if not cites:
                pre_removed += 1
                continue
            to_check.append(ClaimToCheck(len(to_check) + 1, sent.text, {c: excerpts[c] for c in cites}))

    verifier = VerifierAgent(session, client=client)
    verdicts = {v.claim_id: v for v in verifier.run(VerifierInput(to_check)).output.verdicts} if to_check else {}

    version = (session.scalar(select(func.max(Memo.version)).where(Memo.company_id == company.id)) or 0) + 1
    memo = Memo(company_id=company.id, created_at=datetime.now(UTC), version=version, body_md="",
                claims_json=[], verifier_summary_json={})
    session.add(memo)
    session.flush()

    counts = {"supported": 0, "rewritten": 0, "removed_partial": 0, "unsupported": 0, "uncited_dropped": pre_removed}
    published: dict[str, str] = {}  # sentence text -> published text ("" when removed)
    claims: list[Claim] = []
    for c in to_check:
        v = verdicts.get(c.claim_id)
        verdict = v.verdict if v else "unsupported"
        note = v.note if v else "no verdict returned"
        text = c.text
        if verdict == "supported":
            counts["supported"] += 1
        elif verdict == "partial" and v and v.rewrite.strip():
            check, why = heuristic_verdict(v.rewrite, " ".join(c.excerpts.values()))
            if check == "supported":
                text, verdict, note = v.rewrite.strip(), "partial", f"rewritten: {note}"
                counts["rewritten"] += 1
            else:
                counts["removed_partial"] += 1
                note = f"rewrite rejected ({why}); removed: {note}"
        else:
            counts["unsupported" if verdict == "unsupported" else "removed_partial"] += 1
        kept = verdict == "supported" or note.startswith("rewritten")
        published[c.text] = text if kept else ""
        for sid in c.excerpts:
            claims.append(Claim(memo_id=memo.id, text=text, snapshot_id=sid,
                                verdict=verdict if kept else f"removed:{verdict}", verifier_note=note))
    session.add_all(claims)

    memo.body_md = render(company, as_of, draft.output, published, excerpts)
    total = len(to_check)
    memo.claims_json = [{"id": c.claim_id, "text": c.text, "published": published.get(c.text, ""),
                         "citations": list(c.excerpts), "verdict": verdicts[c.claim_id].verdict if c.claim_id in verdicts else None,
                         "note": verdicts[c.claim_id].note if c.claim_id in verdicts else None} for c in to_check]
    memo.verifier_summary_json = {
        **counts, "checked": total,
        "supported_rate": round(counts["supported"] / total, 3) if total else 0.0,
        "research_status": draft.status, "research_run_id": draft.run_id,
        "verifier_status": "skipped" if not to_check else "ran",
        "prompt_versions": f"{research.prompt_version}, {verifier.prompt_version}",
    }
    session.flush()
    return PublishedMemo(memo, claims, memo.verifier_summary_json)


def render(company: Company, as_of: date, draft: MemoDraft, published: dict[str, str],
           excerpts: dict[int, str]) -> str:
    lines = [f"# {company.name}", "", f"_First-look memo as of {as_of}. Public data only. "
             "Every factual sentence links to its source; a separate verifier removed unsupported claims._", ""]
    for s in draft.sections:
        out = []
        for sent in s.sentences:
            if sent.kind == "analysis":
                out.append(sent.text)
                continue
            text = published.get(sent.text, "")
            if text:
                marks = "".join(f" [c:{c}]" for c in sent.citations if c in excerpts)
                out.append(text.rstrip() + marks)
        lines += [f"## {s.heading}", "", " ".join(out) if out else "_Nothing in the public record survived verification._", ""]
    lines += ["## Founder-call questions", ""] + [f"{i}. {q}" for i, q in enumerate(draft.founder_call_questions, 1)]
    return "\n".join(lines) + "\n"
