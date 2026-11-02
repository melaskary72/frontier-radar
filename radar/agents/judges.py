"""LLM judges (Haiku) that plug into entity resolution and the thesis classifier.

Both return callables with the signatures those modules expect, so the LLM stays optional:
without a client the resolver queues ambiguous pairs for a person and the classifier keeps
its own probability.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from radar.agents.base import Agent
from radar.entities.resolve import Mention


class EntityVerdict(BaseModel):
    same_entity: bool
    confidence: float = Field(ge=0, le=1)
    rationale: str


@dataclass
class EntityPair:
    a: str
    b: str


class EntityJudge(Agent[EntityPair, EntityVerdict]):
    name = "entity_judge"
    prompt_file = "entity_judge.v1.md"
    output_model = EntityVerdict

    def build_user(self, inp: EntityPair) -> str:
        return f"Record A: {inp.a}\nRecord B: {inp.b}"

    def fallback(self, inp: EntityPair) -> EntityVerdict:
        return EntityVerdict(same_entity=False, confidence=0.0, rationale="no judge available; queued for review")


class ThesisVerdict(BaseModel):
    probability: float = Field(ge=0, le=1)
    rationale: str


class ThesisJudge(Agent[str, ThesisVerdict]):
    name = "thesis_judge"
    prompt_file = "thesis_judge.v1.md"
    output_model = ThesisVerdict

    def build_user(self, inp: str) -> str:
        return f"Text:\n{inp[:1500]}"

    def fallback(self, inp: str) -> ThesisVerdict:
        return ThesisVerdict(probability=0.5, rationale="no judge available")


def entity_judge(session: Session, client: object = "default"):  # type: ignore[no-untyped-def]
    agent = EntityJudge(session, client=client)

    def judge(a: Mention, b: Mention) -> tuple[bool, float, str]:
        v = agent.run(EntityPair(a.label, b.label)).output
        return v.same_entity, v.confidence, v.rationale

    return judge


def thesis_judge(session: Session, client: object = "default"):  # type: ignore[no-untyped-def]
    agent = ThesisJudge(session, client=client)

    def judge(text: str) -> float:
        return agent.run(text).output.probability

    return judge
