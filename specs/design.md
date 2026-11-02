# Design

Verified: 2026-11-01 (Phase 1: storage and collectors). Later sections are filled as each
phase lands.

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
