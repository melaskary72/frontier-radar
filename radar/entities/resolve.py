"""Entity resolution: turn source mentions into companies.

A *mention* is any source row that implies an organization: a GitHub owner, a Launch HN or
Show HN post, a federal award recipient, a Form D filer. Resolution runs in three tiers:

1. Deterministic: same domain, same GitHub login, or the same distinctive normalized name.
2. Fuzzy: rapidfuzz on loose-normalized names inside blocks (shared first token); a high
   score merges unless the two mentions carry conflicting domains.
3. Ambiguous pairs (mid scores, or short generic names) go to an LLM judge when one is
   configured, and to the review queue otherwise or when the judge is below 0.8 confidence.

Human decisions in `entity_candidates` are hard constraints: approved pairs always merge,
rejected pairs never do.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import cache
from urllib.parse import urlparse

from rapidfuzz import fuzz
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from radar.store.models import (
    Award,
    Company,
    Discovery,
    Edge,
    EntityCandidate,
    Filing,
    HNPost,
    Repo,
)

LEGAL_SUFFIXES = {"inc", "incorporated", "llc", "l l c", "corp", "corporation", "co", "company",
                  "ltd", "limited", "plc", "pbc", "gmbh", "sa", "pte", "lp", "llp"}
LOOSE_SUFFIXES = LEGAL_SUFFIXES | {"labs", "lab", "technologies", "technology", "tech", "systems",
                                   "robotics", "ai", "hq", "io", "industries", "group", "the"}
# Hosts that never identify a company.
GENERIC_HOSTS = {"github.com", "gitlab.com", "twitter.com", "x.com", "linkedin.com", "medium.com",
                 "substack.com", "youtube.com", "youtu.be", "notion.site", "google.com",
                 "docs.google.com", "huggingface.co", "arxiv.org", "vercel.app", "netlify.app",
                 "github.io", "producthunt.com", "ycombinator.com", "news.ycombinator.com",
                 "apps.apple.com", "play.google.com", "discord.gg", "discord.com", "reddit.com",
                 "doi.org", "wikipedia.org", "en.wikipedia.org", "bit.ly", "linktr.ee", "t.co",
                 "researchgate.net", "scholar.google.com", "orcid.org", "zenodo.org", "kaggle.com",
                 "colab.research.google.com", "drive.google.com", "dropbox.com", "figma.com",
                 "pypi.org", "npmjs.com", "crates.io", "readthedocs.io", "gitbook.io",
                 "itch.io", "streamlit.app", "hf.co", "openreview.net", "ieee.org", "sciencedirect.com"}
GENERIC_SUFFIX_HOSTS = (".github.io", ".vercel.app", ".netlify.app", ".pages.dev", ".substack.com",
                        ".notion.site", ".herokuapp.com", ".fly.dev", ".onrender.com", ".readthedocs.io",
                        ".streamlit.app", ".hf.space", ".gitbook.io", ".wordpress.com", ".blogspot.com",
                        ".edu", ".ac.uk", ".gov", ".mil")

AUTO_MERGE = 93.0
AMBIGUOUS_LOW = 85.0
LLM_ACCEPT = 0.8


# --------------------------------------------------------------------------- normalization

def _tokens(name: str) -> list[str]:
    s = name.lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return s.split()


def strict_name(name: str) -> str:
    toks = _tokens(name)
    while toks and toks[-1] in LEGAL_SUFFIXES:
        toks.pop()
    return " ".join(toks)


def loose_name(name: str) -> str:
    toks = [t for t in _tokens(name) if t not in LOOSE_SUFFIXES]
    return " ".join(toks) or strict_name(name)


# Words that describe a field rather than name a company, on top of LOOSE_SUFFIXES.
FIELD_WORDS = {"open", "source", "opensource", "and", "of", "for", "project", "team", "solutions",
               "innovations", "research", "engineering", "services", "international", "global"}


@cache
def thesis_vocabulary() -> frozenset[str]:
    """Every token of every sector keyword, title term, and award keyword in config/thesis.yaml."""
    from radar.config import thesis

    words: set[str] = set()
    for sec in thesis().sectors.values():
        for phrase in [*sec.keywords, *sec.title_terms, *sec.sbir_keywords]:
            words.update(_tokens(phrase))
    return frozenset(words)


def has_distinctive_core(name: str) -> bool:
    """True when the loose name keeps a token that is not a suffix, a field word, or thesis
    vocabulary, and is long enough to be unlikely to collide. A name made only of field words
    ("Command and Control", "Additive Manufacturing", "Open Source") names a field, not a company."""
    generic = LOOSE_SUFFIXES | FIELD_WORDS | thesis_vocabulary()
    toks = loose_name(name).split()
    core = [t for t in toks if t not in generic]
    return bool(core) and (len(toks) >= 2 or len(toks[0]) >= 8)


def normalize_domain(url_or_host: str | None) -> str | None:
    if not url_or_host:
        return None
    u = url_or_host.strip().lower()
    if "://" not in u:
        u = "http://" + u
    host = (urlparse(u).hostname or "").removeprefix("www.")
    if not host or "." not in host:
        return None
    if host in GENERIC_HOSTS or host.endswith(GENERIC_SUFFIX_HOSTS):
        return None
    return host


def github_org_from_url(url: str | None) -> str | None:
    if not url:
        return None
    p = urlparse(url if "://" in url else "http://" + url)
    if (p.hostname or "").removeprefix("www.") != "github.com":
        return None
    parts = [x for x in p.path.split("/") if x]
    return parts[0].lower() if parts else None


EN_DASH = chr(0x2013)  # Launch HN titles separate name and tagline with an en dash
LAUNCH_TITLE = re.compile(
    r"^(?:launch|show) hn:\s*(.+?)(?:\s+\((?:yc\s+)?[a-z]\d{2}\)|\s+[-:," + EN_DASH + r"]|$)", re.I
)


def name_from_hn_title(title: str) -> str | None:
    m = LAUNCH_TITLE.match(title.strip())
    if not m:
        return None
    name = m.group(1).strip().strip("\"'")
    # "Show HN: I built X" and other sentence-like titles are not names.
    if len(name.split()) > 4 or name.lower().startswith(("i ", "we ", "my ", "a ", "an ", "the ")):
        return None
    return name


def is_academic(name: str, login: str | None, domain: str | None, text: str = "") -> bool:
    """Research group heuristic: university words, university acronyms as whole tokens in the
    name or login, or an academic domain. Singular "Lab" with an acronym also counts."""
    from radar.config import sources

    cfg = sources()["academic_markers"]
    blob = f"{name} {login or ''} {text}".lower()
    # Whole words only: "research lab" must not match "educational robots for research
    # laboratories" (Robonine's profile, a false positive found on 2026-11-03).
    if any(re.search(rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])", blob) for w in cfg["words"]):
        return True
    toks = set(re.split(r"[^a-z0-9]+", f"{name} {login or ''}".lower()))
    if toks & set(cfg["acronyms"]):
        return True
    host = (domain or "").lower()
    if any(host.endswith(sfx) for sfx in cfg["domain_suffixes"]):
        return True
    return any(re.search(p, host) for p in cfg.get("domain_patterns", []))


def classify_profile(login: str, profile: dict | None) -> str | None:
    """'lab' or 'incumbent' from a GitHub user or organization profile (name, description or
    bio, company, website, location, email domain), else None. University groups usually say
    who they are here even when the login is an opaque acronym (cvg, prbonn)."""
    if not profile:
        return None
    name = str(profile.get("name") or "")
    text = " ".join(str(profile.get(k) or "") for k in ("description", "bio", "company", "location"))
    hosts = [normalize_domain(str(profile.get("blog") or "")), (str(profile.get("email") or "").partition("@")[2] or None)]
    if is_academic(name, login, None, text) or any(is_academic("", None, h) for h in hosts if h):
        return "lab"
    if is_incumbent(name, login, f"{profile.get('company') or ''} {profile.get('description') or ''}"):
        return "incumbent"
    return None


def is_incumbent(name: str, login: str | None, text: str = "") -> bool:
    """Organizations on the top_labs list are talent sources, not investment targets."""
    from radar.config import sources

    labs = sources()["top_labs"]
    blob = f"{name} {login or ''} {text}"
    return any(re.search(rf"(?<![a-z0-9]){re.escape(lab)}(?![a-z0-9])", blob, re.I) for lab in labs)


def distinctive(name: str) -> bool:
    """Exact name matches are trusted only for names unlikely to collide."""
    s = strict_name(name)
    return len(s.split()) >= 2 or len(s) >= 8


# --------------------------------------------------------------------------- mentions

@dataclass
class Mention:
    key: str  # "repos-owner:login", "hn_posts:12", "awards:5", "filings:7"
    source: str
    name: str
    kind: str
    domain: str | None = None
    github_login: str | None = None
    location: str | None = None
    first_seen_at: datetime | None = None
    rows: list[tuple[str, int, int | None]] = field(default_factory=list)  # (table, id, snapshot)
    sectors: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        bits = [self.name, f"[{self.source}]"]
        if self.domain:
            bits.append(self.domain)
        if self.github_login:
            bits.append(f"gh:{self.github_login}")
        return " ".join(bits)


SQL_CHUNK = 10_000


def _sectors(session: Session, table: str, ids: Iterable[int]) -> dict[int, list[str]]:
    out: dict[int, list[str]] = defaultdict(list)
    ids = list(ids)
    if not ids:
        return out
    # Chunked: SQLite caps bound variables at 32,766, and the backtest has 200K+ repositories.
    for i in range(0, len(ids), SQL_CHUNK):
        for row_id, sector in session.execute(
            select(Discovery.row_id, Discovery.sector).where(Discovery.table_name == table,
                                                             Discovery.row_id.in_(ids[i:i + SQL_CHUNK]))
        ):
            out[row_id].append(sector)
    return out


def _min_dt(a: datetime | None, b: datetime | None) -> datetime | None:
    if a is None:
        return b
    if b is None:
        return a
    a = a if a.tzinfo else a.replace(tzinfo=UTC)
    b = b if b.tzinfo else b.replace(tzinfo=UTC)
    return min(a, b)


def collect_mentions(session: Session, owner_profiles: dict[str, dict] | None = None) -> list[Mention]:
    """Build mentions from every source table. `owner_profiles` maps GitHub login to the
    cached /users/{login} payload (name, blog, location) when it has been fetched."""
    owner_profiles = owner_profiles or {}
    mentions: list[Mention] = []

    repos = session.scalars(select(Repo)).all()
    repo_sectors = _sectors(session, "repos", [r.id for r in repos])
    by_owner: dict[str, Mention] = {}
    for r in repos:
        login = r.owner.lower()
        m = by_owner.get(login)
        if m is None:
            prof = owner_profiles.get(login) or {}
            m = Mention(
                key=f"gh:{login}", source="github", name=prof.get("name") or r.owner,
                kind="org" if r.owner_type == "Organization" else "user",
                domain=normalize_domain(prof.get("blog")) or None, github_login=login,
                location=prof.get("location"),
            )
            by_owner[login] = m
        if m.domain is None and r.homepage:
            m.domain = normalize_domain(r.homepage)
        m.first_seen_at = _min_dt(m.first_seen_at, r.created_at)
        m.rows.append(("repos", r.id, r.snapshot_id))
        m.sectors += repo_sectors.get(r.id, [])
    # A domain claimed by two different GitHub owners is shared infrastructure (a lab page,
    # a link shortener), not a company identity.
    claims = defaultdict(set)
    for m in by_owner.values():
        if m.domain:
            claims[m.domain].add(m.github_login)
    for m in by_owner.values():
        if m.domain and len(claims[m.domain]) > 1:
            m.domain = None
    mentions += by_owner.values()

    posts = session.scalars(select(HNPost)).all()
    post_sectors = _sectors(session, "hn_posts", [p.id for p in posts])
    for p in posts:
        name = name_from_hn_title(p.title)
        if not name:
            continue
        mentions.append(Mention(
            key=f"hn:{p.id}", source="hn", name=name, kind="launch",
            domain=normalize_domain(p.url), github_login=github_org_from_url(p.url),
            first_seen_at=p.created_at, rows=[("hn_posts", p.id, p.snapshot_id)],
            sectors=post_sectors.get(p.id, []),
        ))

    # One mention per distinct recipient (or filer CIK), carrying all of its rows.
    awards = session.scalars(select(Award)).all()
    award_sectors = _sectors(session, "awards", [a.id for a in awards])
    by_recipient: dict[str, Mention] = {}
    for a in awards:
        key = strict_name(a.recipient or "")
        if not key:
            continue
        m = by_recipient.get(key)
        if m is None:
            m = by_recipient[key] = Mention(key=f"award:{key}", source="award", name=a.recipient.title(),
                                            kind="awardee")
        if a.award_date:
            m.first_seen_at = _min_dt(m.first_seen_at, datetime.combine(a.award_date, datetime.min.time(), UTC))
        m.rows.append(("awards", a.id, a.snapshot_id))
        m.sectors += award_sectors.get(a.id, [])
    mentions += by_recipient.values()

    by_cik: dict[str, Mention] = {}
    for f in session.scalars(select(Filing)).all():
        m = by_cik.get(f.cik)
        if m is None:
            m = by_cik[f.cik] = Mention(key=f"filing:{f.cik}", source="filing", name=f.company_name.title(),
                                        kind="filer")
        m.first_seen_at = _min_dt(m.first_seen_at, datetime.combine(f.filed_at, datetime.min.time(), UTC))
        m.rows.append(("filings", f.id, f.snapshot_id))
    mentions += by_cik.values()
    return mentions


# --------------------------------------------------------------------------- union-find

class UnionFind:
    def __init__(self, keys: Iterable[str]):
        self.parent = {k: k for k in keys}
        # How each mention joined its cluster (the weakest link it took part in).
        self.joined_by: dict[str, tuple[str, float]] = {}

    def find(self, k: str) -> str:
        while self.parent[k] != k:
            self.parent[k] = self.parent[self.parent[k]]
            k = self.parent[k]
        return k

    def union(self, a: str, b: str, method: str, confidence: float) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra
            for k in (a, b):
                prev = self.joined_by.get(k)
                if prev is None or confidence < prev[1]:
                    self.joined_by[k] = (method, confidence)


@dataclass
class Decision:
    a: Mention
    b: Mention
    score: float
    verdict: str  # merge | separate | ambiguous
    method: str
    confidence: float
    rationale: str


Judge = Callable[[Mention, Mention], tuple[bool, float, str]]


def compare(a: Mention, b: Mention) -> Decision | None:
    """Tier 1 and tier 2 decision for one pair, or None if the pair is not comparable."""
    if a.domain and b.domain and a.domain == b.domain:
        return Decision(a, b, 100, "merge", "deterministic", 1.0, f"same domain {a.domain}")
    # Two different domains, or two different GitHub accounts, are two different entities.
    conflicting_domains = bool(a.domain and b.domain) or bool(
        a.github_login and b.github_login and a.github_login != b.github_login)
    both_logins = bool(a.github_login and b.github_login)
    if both_logins and a.github_login == b.github_login:
        return Decision(a, b, 100, "merge", "deterministic", 1.0, f"same GitHub login {a.github_login}")
    login_matches_name = (a.github_login == loose_name(b.name).replace(" ", "")) or (
        b.github_login == loose_name(a.name).replace(" ", ""))
    if not both_logins and login_matches_name and not conflicting_domains:
        return Decision(a, b, 96, "ambiguous", "fuzzy", 0.7, "name equals the other side's GitHub login")

    sa, sb = strict_name(a.name), strict_name(b.name)
    if sa and sa == sb:
        if conflicting_domains:
            return Decision(a, b, 100, "separate", "deterministic", 0.9, "same name, different domains or accounts")
        if distinctive(a.name):
            return Decision(a, b, 100, "merge", "deterministic", 0.97, f"same distinctive name '{sa}'")
        return Decision(a, b, 100, "ambiguous", "fuzzy", 0.6, f"same short name '{sa}'")

    la, lb = loose_name(a.name), loose_name(b.name)
    if not la or not lb:
        return None
    score = float(fuzz.token_sort_ratio(la, lb))
    if score < AMBIGUOUS_LOW:
        return None
    if conflicting_domains:
        return Decision(a, b, score, "separate", "fuzzy", 0.9, "similar names, different domains or accounts")
    if score >= AUTO_MERGE and has_distinctive_core(a.name) and has_distinctive_core(b.name):
        return Decision(a, b, score, "merge", "fuzzy", score / 100, f"fuzzy {score:.0f} on '{la}' vs '{lb}'")
    return Decision(a, b, score, "ambiguous", "fuzzy", score / 100, f"fuzzy {score:.0f} on '{la}' vs '{lb}'")


def candidate_pairs(mentions: list[Mention]) -> Iterable[tuple[Mention, Mention]]:
    """Blocking: only compare mentions that share a domain, a GitHub login, or the first
    token of their loose name. Keeps resolution near-linear."""
    blocks: dict[str, list[Mention]] = defaultdict(list)
    for m in mentions:
        if m.domain:
            blocks[f"d:{m.domain}"].append(m)
        if m.github_login:
            blocks[f"g:{m.github_login}"].append(m)
        ln = loose_name(m.name)
        if ln:
            blocks[f"n:{ln.split()[0]}"].append(m)
            blocks[f"n:{ln.replace(' ', '')}"].append(m)
    seen: set[tuple[str, str]] = set()
    for members in blocks.values():
        if len(members) > 200:  # a generic first token; too broad to be informative
            continue
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                if a.key == b.key or a.source == b.source == "github":
                    continue
                pair = (a.key, b.key) if a.key < b.key else (b.key, a.key)
                if pair not in seen:
                    seen.add(pair)
                    yield a, b


# --------------------------------------------------------------------------- resolve

@dataclass
class ResolveResult:
    companies: int
    edges: int
    merges: dict[str, int]
    queued: int
    decisions: list[Decision]


def _human_constraints(session: Session) -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
    approved: set[tuple[str, str]] = set()
    rejected: set[tuple[str, str]] = set()
    for c in session.scalars(select(EntityCandidate).where(EntityCandidate.status != "pending")):
        pair = (c.a_key, c.b_key)
        (approved if c.status == "approved" else rejected).add(pair)
    return approved, rejected


def _entity_key(members: list[Mention]) -> str:
    for m in members:
        if m.domain:
            return f"domain:{m.domain}"
    for m in members:
        if m.github_login:
            return f"github:{m.github_login}"
    return f"name:{strict_name(members[0].name)}:{members[0].key}"


def resolve(session: Session, *, judge: Judge | None = None,
            owner_profiles: dict[str, dict] | None = None) -> ResolveResult:
    mentions = collect_mentions(session, owner_profiles)
    by_key = {m.key: m for m in mentions}
    uf = UnionFind(by_key)
    approved, rejected = _human_constraints(session)
    for key_a, key_b in approved:
        if key_a in by_key and key_b in by_key:
            uf.union(key_a, key_b, "human", 1.0)

    decisions: list[Decision] = []
    queued = 0
    merges: dict[str, int] = defaultdict(int)
    now = datetime.now(UTC)
    for a, b in candidate_pairs(mentions):
        pair = (a.key, b.key) if a.key < b.key else (b.key, a.key)
        if pair in rejected or pair in approved:
            continue
        d = compare(a, b)
        if d is None:
            continue
        if d.verdict == "ambiguous" and judge is not None:
            same, conf, why = judge(a, b)
            d = Decision(a, b, d.score, "merge" if same and conf >= LLM_ACCEPT else
                         ("separate" if not same and conf >= LLM_ACCEPT else "ambiguous"),
                         "llm", conf, why)
        decisions.append(d)
        if d.verdict == "merge":
            uf.union(a.key, b.key, d.method, d.confidence)
            merges[d.method] += 1
        elif d.verdict == "ambiguous":
            queued += _queue(session, pair, by_key, d, now)

    # Materialize: one company per cluster, edges to every row of every member mention.
    clusters: dict[str, list[Mention]] = defaultdict(list)
    for m in mentions:
        clusters[uf.find(m.key)].append(m)

    session.execute(delete(Edge).where(Edge.src_type == "company", Edge.method != "human"))
    existing = {c.entity_key: c for c in session.scalars(select(Company))}
    plans: list[tuple[str, list[Mention]]] = []
    for members in clusters.values():
        members.sort(key=lambda m: ({"github": 0, "hn": 1, "award": 2, "filing": 3}[m.source], m.key))
        plans.append((_entity_key(members), members))
    # A split or a new merge can move a domain or GitHub org to a different company row. Release
    # identifiers held by any other row before inserting, or the unique constraint fires before
    # stale rows are retired below.
    claimed_domain = {dom: k for k, ms in plans if (dom := next((m.domain for m in ms if m.domain), None))}
    claimed_org = {org: k for k, ms in plans if (org := next((m.github_login for m in ms if m.github_login), None))}
    for c in existing.values():
        if c.domain and claimed_domain.get(c.domain) != c.entity_key:
            c.domain = None
        if c.github_org and claimed_org.get(c.github_org) != c.entity_key:
            c.github_org = None
    session.flush()
    produced: set[str] = set()
    n_edges = 0
    for key, members in plans:
        produced.add(key)
        primary = members[0]
        sectors = [s for m in members for s in m.sectors]
        sector = max(set(sectors), key=sectors.count) if sectors else None
        first_seen = None
        for m in members:
            first_seen = _min_dt(first_seen, m.first_seen_at)
        company = existing.get(key)
        if company is None:
            company = Company(entity_key=key, first_seen_at=first_seen or now)
            session.add(company)
        company.name = next((m.name for m in members if m.source == "hn"), primary.name)
        company.kind = primary.kind
        if company.kind in ("org", "user") and is_academic(primary.name, primary.github_login,
                                                           next((m.domain for m in members if m.domain), None)):
            company.kind = "lab"
        company.domain = next((m.domain for m in members if m.domain), None)
        company.github_org = next((m.github_login for m in members if m.github_login), None)
        company.sector = sector
        company.location = next((m.location for m in members if m.location), None)
        if first_seen:
            company.first_seen_at = first_seen
        session.flush()
        for m in members:
            method, conf = uf.joined_by.get(m.key, ("deterministic", 1.0))
            for table, row_id, snap in m.rows:
                session.add(Edge(src_type="company", src_id=company.id, dst_type=table, dst_id=row_id,
                                 relation="has_evidence", confidence=conf,
                                 evidence_snapshot_id=snap, method=method))
                n_edges += 1
    session.flush()
    _retire_stale(session, [c for k, c in existing.items() if k not in produced])
    return ResolveResult(len(clusters), n_edges, dict(merges), queued, decisions)


def _retire_stale(session: Session, stale: list[Company]) -> None:
    """Companies absorbed by a merge: delete them, unless a person's work references them
    (pipeline card, feedback, memo), in which case keep the row and mark it stale."""
    from radar.store.models import Feature, FeedbackRow, Memo, PipelineItem, Score

    for c in stale:
        referenced = any(session.scalars(select(model.id).where(model.company_id == c.id)).first()
                         for model in (PipelineItem, FeedbackRow, Memo))
        if referenced:
            c.kind = "stale"
            continue
        session.execute(delete(Edge).where(Edge.src_type == "company", Edge.src_id == c.id))
        session.execute(delete(Feature).where(Feature.company_id == c.id))
        session.execute(delete(Score).where(Score.company_id == c.id))
        session.delete(c)
    session.flush()


def _queue(session: Session, pair: tuple[str, str], by_key: dict[str, Mention], d: Decision,
           now: datetime) -> int:
    exists = session.scalars(select(EntityCandidate).filter_by(a_key=pair[0], b_key=pair[1])).first()
    if exists is not None:
        return 0
    session.add(EntityCandidate(
        a_key=pair[0], b_key=pair[1], a_label=by_key[pair[0]].label, b_label=by_key[pair[1]].label,
        score=d.score, status="pending", confidence=d.confidence, rationale=d.rationale,
        created_at=now, decided_by="llm" if d.method == "llm" else None,
    ))
    return 1
