# Design

Verified: 2026-11-01 (Phases 1 to 3). Later sections are filled as each phase lands.

## Data flow

```
collectors ──> raw_snapshots (fetched_at, sha256, payload)
     │
     └──> typed tables: repos, repo_events, papers, persons, companies, filings,
          awards, job_postings, hn_posts, domain_ranks
                 │
       entities/resolve ──> edges (method, confidence, evidence_snapshot_id)
                 │
       scoring/features (as_of date) ──> features ──> scores (+ breakdown)
                 │
       agents: sourcing ─> research memo ─> verifier ─> published memo + claims
                 │
       digest, email, audio, dashboard
```

## Collector contract

All collectors extend `radar.collectors.base.HttpCollector`:

1. `fetch()` builds a request key (SHA-256 of method, full URL, JSON body) and returns a
   fresh cached snapshot if one exists inside the TTL (time to live).
2. Otherwise it waits on the source's rate limiter, sends, and retries 429 and 5xx with
   exponential backoff (honouring `Retry-After`), up to five attempts.
3. The raw body is hashed and stored; identical content for the same request is not stored
   twice.
4. `RADAR_OFFLINE=1` never touches the network. `RADAR_REPLAY_DIR` and `RADAR_RECORD_DIR`
   switch the HTTP transport to cassette replay or recording.

Recorded cassettes live in `tests/fixtures/<source>/`. Large list responses are trimmed to
a few items (marked with a `trimmed` field); request headers are never written.

## Point-in-time rules

A feature at `as_of = T` may read only rows whose own timestamp is on or before T:
`repo_events.at`, `papers.submitted_at`, `filings.filed_at`, `awards.award_date`,
`hn_posts.created_at`, `domain_ranks.list_date`, and OpenAlex publication dates.
Sources without history (job boards, current citation counts, current star totals) are
marked unavailable in the backtest. See BUILD_CONTRACT.md, deviations D1 to D3.

## Storage

One SQLAlchemy schema (`radar/store/models.py`) for SQLite locally and Neon Postgres for
the deployed dashboard; Alembic migrations in `migrations/`. `tests/test_migrations.py`
asserts the migration head matches the models exactly.

## Entity resolution (Phase 2)

Mentions: GitHub owners, Launch HN and Show HN posts, award recipients (one mention per
recipient), Form D filers (one per CIK). Blocking on domain, GitHub login, and the first
token of the loose name keeps comparisons near-linear. Tiers:

1. Deterministic: same domain, same GitHub login, or the same distinctive strict name.
2. Fuzzy: `token_sort_ratio` on loose names; 93 or more merges when both names are
   distinctive and nothing conflicts; 85 to 93 is ambiguous.
3. Ambiguous pairs go to the LLM judge (0.8 confidence to act) or the review queue.

Different domains or different GitHub accounts always mean different entities. Domains
claimed by two GitHub owners are generic. Company identity (`entity_key`) is the domain,
then the GitHub login, then the name, so reruns keep ids stable.

## Scoring (Phase 2)

1. Prescore every company with hindsight-free signals (thesis probability, distinct thesis
   queries that found it, organization or launch, award or launch on or before T).
2. Enrich the top N under a request budget: forks, issues, pull requests, releases, commits,
   the tree at the last commit on or before T, OpenAlex affiliations, Form D by exact name;
   live mode adds Events API stars, job boards, and Tranco rank.
3. Features (radar/scoring/features.py) read only rows timestamped on or before T. Each
   has a `pit` flag; backtests drop the ones without history.
4. Percentile within (sector, T); zero or missing is 0. Group score is the mean of its
   available features; group weights renormalize over available groups.
5. Explanations list group contributions and the top three evidence items.

## Agents (Phase 3)

| Agent | Model | Input | Output | Fallback |
|---|---|---|---|---|
| Sourcing | Haiku 5.5, effort low | score breakdown, evidence pack | why now (3 sentences max), sector, confidence, evidence ids (must be from the pack) | top evidence labels |
| Research | Sonnet 5.5, effort medium | evidence pack | seven fixed sections of fact and analysis sentences; facts cite snapshot ids; 8 to 10 founder-call questions | one cited fact per evidence item |
| Verifier | Sonnet 5.5, effort medium | each claim with only its cited excerpts | supported, partial (with rewrite), or unsupported per claim | strict heuristic: every number and proper noun must appear in the excerpt |
| Digest | Haiku 5.5, effort low | top new, movers, verifier stats, spend | subject, email markdown, 240 to 320 word audio script | template |
| Entity judge | Haiku 5.5 | two mention labels | same entity, confidence, rationale | queue for review |
| Thesis judge | Haiku 5.5 | company text | probability, rationale | classifier probability stands |

Publishing rule: supported claims are kept; partial claims are replaced by the verifier's
rewrite only when the rewrite passes the strict heuristic against the same excerpts;
everything else is removed and recorded in `claims` with its verdict and note.
