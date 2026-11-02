"""SQLAlchemy models. One schema for SQLite (local) and Postgres (deployed dashboard).

Every derived row points back to the raw snapshot it came from, so any number on the
dashboard can be traced to a cached HTTP response with its fetch time and SHA-256 hash.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, ClassVar

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict[Any, Any]] = {dict[str, Any]: JSON, list[Any]: JSON}


class RawSnapshot(Base):
    __tablename__ = "raw_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    url: Mapped[str] = mapped_column(Text)
    request_key: Mapped[str] = mapped_column(String(64), index=True)  # sha256(method, url, body)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    content_sha256: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[Any] = mapped_column(JSON)
    http_status: Mapped[int] = mapped_column(Integer)


class Person(Base):
    __tablename__ = "persons"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(256), index=True)
    github_login: Mapped[str | None] = mapped_column(String(128), unique=True)
    s2_author_id: Mapped[str | None] = mapped_column(String(32), unique=True)
    openalex_id: Mapped[str | None] = mapped_column(String(32), unique=True)
    orcid: Mapped[str | None] = mapped_column(String(32), unique=True)
    affiliations_json: Mapped[list[Any]] = mapped_column(JSON, default=list)
    bio: Mapped[str | None] = mapped_column(Text)
    # Current Semantic Scholar metrics: live radar only (not observable point-in-time).
    citation_count: Mapped[int | None] = mapped_column(Integer)
    h_index: Mapped[int | None] = mapped_column(Integer)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PersonAffiliation(Base):
    """Dated affiliation from an OpenAlex authorship (publication date, institution)."""

    __tablename__ = "person_affiliations"
    __table_args__ = (UniqueConstraint("person_id", "at", "institution"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("persons.id"), index=True)
    at: Mapped[date] = mapped_column(Date)
    institution: Mapped[str] = mapped_column(String(300))
    snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("raw_snapshots.id"))


class Company(Base):
    """A candidate entity: a startup, an organization on GitHub, an awardee, or a filer.

    `entity_key` is the stable identity chosen by entity resolution (domain, then GitHub
    login, then normalized name) so reruns keep the same id.
    """

    __tablename__ = "companies"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity_key: Mapped[str] = mapped_column(String(300), unique=True)
    kind: Mapped[str] = mapped_column(String(16), default="org")  # org | user | launch | awardee | filer
    name: Mapped[str] = mapped_column(String(256), index=True)
    domain: Mapped[str | None] = mapped_column(String(256), unique=True)
    github_org: Mapped[str | None] = mapped_column(String(128), unique=True)
    sector: Mapped[str | None] = mapped_column(String(32), index=True)
    stage_guess: Mapped[str | None] = mapped_column(String(32))
    location: Mapped[str | None] = mapped_column(String(256))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Repo(Base):
    __tablename__ = "repos"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    full_name: Mapped[str] = mapped_column(String(256), unique=True)
    owner: Mapped[str] = mapped_column(String(128), index=True)
    owner_type: Mapped[str | None] = mapped_column(String(16))  # User | Organization
    description: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    pushed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stars: Mapped[int] = mapped_column(Integer, default=0)
    forks: Mapped[int] = mapped_column(Integer, default=0)
    contributors: Mapped[int | None] = mapped_column(Integer)
    last_commit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    topics_json: Mapped[list[Any]] = mapped_column(JSON, default=list)
    homepage: Mapped[str | None] = mapped_column(Text)
    snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("raw_snapshots.id"))


class RepoEvent(Base):
    """One timestamped repository event: the point-in-time momentum signal.

    kinds: fork, issue, pr, release, commit (all with historical timestamps), and star
    (Events API only: last 90 days, so live radar only, never used by the backtest).
    GitHub no longer exposes stargazer lists with starred_at for repositories you do not
    own, so historical star velocity is not observable; see BUILD_CONTRACT.md.
    """

    __tablename__ = "repo_events"
    __table_args__ = (
        UniqueConstraint("repo_full_name", "kind", "external_id"),
        Index("ix_repo_events_repo_kind_at", "repo_full_name", "kind", "at"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    repo_full_name: Mapped[str] = mapped_column(String(256), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    external_id: Mapped[str] = mapped_column(String(64))
    actor: Mapped[str | None] = mapped_column(String(128))
    is_external: Mapped[bool | None] = mapped_column()  # actor outside owner/members
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("raw_snapshots.id"))


class RepoTree(Base):
    """Repository file tree at the last commit on or before `as_of`: point-in-time repo
    quality (tests, CI, license, README) and hardware-adjacent code share."""

    __tablename__ = "repo_trees"
    __table_args__ = (UniqueConstraint("repo_full_name", "sha"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    repo_full_name: Mapped[str] = mapped_column(String(256), index=True)
    sha: Mapped[str] = mapped_column(String(40))
    committed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    signals_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("raw_snapshots.id"))


class Paper(Base):
    __tablename__ = "papers"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    arxiv_id: Mapped[str] = mapped_column(String(32), unique=True)
    title: Mapped[str] = mapped_column(Text)
    abstract: Mapped[str | None] = mapped_column(Text)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    categories_json: Mapped[list[Any]] = mapped_column(JSON, default=list)
    authors_json: Mapped[list[Any]] = mapped_column(JSON, default=list)  # [{name, affiliations}]
    snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("raw_snapshots.id"))


class Filing(Base):
    __tablename__ = "filings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    accession: Mapped[str] = mapped_column(String(32), unique=True)
    cik: Mapped[str] = mapped_column(String(16), index=True)
    company_name: Mapped[str] = mapped_column(String(256), index=True)
    form_type: Mapped[str] = mapped_column(String(16))
    filed_at: Mapped[date] = mapped_column(Date, index=True)
    industry: Mapped[str | None] = mapped_column(String(128))
    total_offering: Mapped[float | None] = mapped_column(Float)
    amount_sold: Mapped[float | None] = mapped_column(Float)
    state: Mapped[str | None] = mapped_column(String(8))
    url: Mapped[str] = mapped_column(Text)
    snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("raw_snapshots.id"))


class Award(Base):
    __tablename__ = "awards"
    __table_args__ = (UniqueConstraint("source", "external_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(16))  # sbir | usaspending
    external_id: Mapped[str] = mapped_column(String(128))
    recipient: Mapped[str] = mapped_column(String(256), index=True)
    agency: Mapped[str | None] = mapped_column(String(128))
    program: Mapped[str | None] = mapped_column(String(64))
    amount: Mapped[float | None] = mapped_column(Float)
    award_date: Mapped[date | None] = mapped_column(Date, index=True)
    topic: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("raw_snapshots.id"))


class JobPosting(Base):
    __tablename__ = "job_postings"
    __table_args__ = (UniqueConstraint("ats", "board", "external_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"), index=True)
    ats: Mapped[str] = mapped_column(String(16))
    board: Mapped[str] = mapped_column(String(128))
    external_id: Mapped[str] = mapped_column(String(128))
    title: Mapped[str] = mapped_column(Text)
    department: Mapped[str | None] = mapped_column(String(256))
    location: Mapped[str | None] = mapped_column(String(256))
    is_engineering: Mapped[bool] = mapped_column(default=False)
    is_hardware: Mapped[bool] = mapped_column(default=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("raw_snapshots.id"))


class HNPost(Base):
    __tablename__ = "hn_posts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    object_id: Mapped[str] = mapped_column(String(32), unique=True)
    kind: Mapped[str] = mapped_column(String(16))  # show_hn | launch_hn
    title: Mapped[str] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    points: Mapped[int] = mapped_column(Integer, default=0)
    num_comments: Mapped[int] = mapped_column(Integer, default=0)
    author: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("raw_snapshots.id"))


class DomainRank(Base):
    __tablename__ = "domain_ranks"
    __table_args__ = (UniqueConstraint("domain", "list_date"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    domain: Mapped[str] = mapped_column(String(256), index=True)
    list_date: Mapped[date] = mapped_column(Date)
    rank: Mapped[int] = mapped_column(Integer)
    snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("raw_snapshots.id"))


class Edge(Base):
    __tablename__ = "edges"
    __table_args__ = (Index("ix_edges_src", "src_type", "src_id"), Index("ix_edges_dst", "dst_type", "dst_id"))
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    src_type: Mapped[str] = mapped_column(String(16))
    src_id: Mapped[int] = mapped_column(Integer)
    dst_type: Mapped[str] = mapped_column(String(16))
    dst_id: Mapped[int] = mapped_column(Integer)
    relation: Mapped[str] = mapped_column(String(32))
    confidence: Mapped[float] = mapped_column(Float)
    evidence_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("raw_snapshots.id"))
    method: Mapped[str] = mapped_column(String(16))  # deterministic | fuzzy | llm | human


class Discovery(Base):
    """Which thesis query surfaced which row. Doubles as the backtest's query audit log:
    every candidate in a universe must trace to a row here, never to a name lookup."""

    __tablename__ = "discoveries"
    __table_args__ = (UniqueConstraint("table_name", "row_id", "sector", "query"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    table_name: Mapped[str] = mapped_column(String(32), index=True)
    row_id: Mapped[int] = mapped_column(Integer, index=True)
    sector: Mapped[str] = mapped_column(String(32), index=True)
    query: Mapped[str] = mapped_column(Text)
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EntityCandidate(Base):
    """An ambiguous match between two mentions, waiting for an LLM or a person."""

    __tablename__ = "entity_candidates"
    __table_args__ = (UniqueConstraint("a_key", "b_key"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    a_key: Mapped[str] = mapped_column(String(300))
    b_key: Mapped[str] = mapped_column(String(300))
    a_label: Mapped[str] = mapped_column(Text)
    b_label: Mapped[str] = mapped_column(Text)
    score: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | approved | rejected
    decided_by: Mapped[str | None] = mapped_column(String(32))  # llm | human
    confidence: Mapped[float | None] = mapped_column(Float)
    rationale: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Feature(Base):
    __tablename__ = "features"
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), primary_key=True)
    as_of: Mapped[date] = mapped_column(Date, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[float | None] = mapped_column(Float)
    evidence_snapshot_ids_json: Mapped[list[Any]] = mapped_column(JSON, default=list)
    evidence_json: Mapped[list[Any]] = mapped_column(JSON, default=list)  # [{snapshot_id, label, url, at}]


class Score(Base):
    __tablename__ = "scores"
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), primary_key=True)
    as_of: Mapped[date] = mapped_column(Date, primary_key=True)
    total: Mapped[float] = mapped_column(Float)
    breakdown_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    rank: Mapped[int] = mapped_column(Integer)


class Memo(Base):
    __tablename__ = "memos"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1)
    body_md: Mapped[str] = mapped_column(Text)
    claims_json: Mapped[list[Any]] = mapped_column(JSON, default=list)
    verifier_summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Claim(Base):
    __tablename__ = "claims"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    memo_id: Mapped[int] = mapped_column(ForeignKey("memos.id"), index=True)
    text: Mapped[str] = mapped_column(Text)
    snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("raw_snapshots.id"))
    verdict: Mapped[str | None] = mapped_column(String(16))  # supported | partial | unsupported
    verifier_note: Mapped[str | None] = mapped_column(Text)


class AgentRun(Base):
    __tablename__ = "agent_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent: Mapped[str] = mapped_column(String(32), index=True)
    model: Mapped[str] = mapped_column(String(64))
    prompt_version: Mapped[str] = mapped_column(String(32))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(16))  # ok | retried | fallback | error | capped
    input_hash: Mapped[str] = mapped_column(String(64))
    output_json: Mapped[Any] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text)


class FeedbackRow(Base):
    __tablename__ = "feedback"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    user_label: Mapped[str] = mapped_column(String(64))
    verdict: Mapped[int] = mapped_column(Integer)  # +1 | -1
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PipelineItem(Base):
    __tablename__ = "pipeline_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), unique=True)
    stage: Mapped[str] = mapped_column(String(16))
    owner: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)


class PortfolioCompany(Base):
    __tablename__ = "portfolio_companies"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(256), unique=True)
    sector: Mapped[str | None] = mapped_column(String(64))
    first_invested_year: Mapped[int | None] = mapped_column(Integer)
    stage_at_entry: Mapped[str | None] = mapped_column(String(64))
    source_url: Mapped[str] = mapped_column(Text)


class PortfolioFact(Base):
    __tablename__ = "portfolio_facts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("portfolio_companies.id"), index=True)
    fact_type: Mapped[str] = mapped_column(String(32))
    value: Mapped[str] = mapped_column(Text)
    as_of: Mapped[date] = mapped_column(Date)
    source_url: Mapped[str] = mapped_column(Text)


class CostLog(Base):
    __tablename__ = "cost_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    agent: Mapped[str] = mapped_column(String(32))
    usd: Mapped[float] = mapped_column(Float)
    cumulative_usd: Mapped[float] = mapped_column(Float)
