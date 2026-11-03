# Tasks

The build plan, phase by phase. Each phase closes with a dated verification stamp: tests
green, lint clean, no em dashes, committed.

## Phase 1: scaffold, specs, contract, configs, schema, collectors

- [x] Repository layout, `pyproject.toml` (Python 3.12, uv), `.env.example`, `.gitignore`
- [x] `BUILD_CONTRACT.md` with canonical sources, scope, out of scope, deviations
- [x] `specs/requirements.md`, `specs/design.md`, `specs/tasks.md`
- [x] `config/thesis.yaml`, `weights.yaml`, `models.yaml`, `sources.yaml`
- [x] SQLAlchemy models for every table in the data model; Alembic initial migration
- [x] Snapshot cache with `fetched_at` and SHA-256; offline mode; cassette record and replay
- [x] Collectors: GitHub, arXiv, Semantic Scholar, OpenAlex, Hacker News, ATS (Greenhouse,
      Lever, Ashby), SEC EDGAR Form D, SBIR.gov, USAspending, Tranco
- [x] Real recorded fixtures and a unit test per collector
- [x] Leakage guard test, no-em-dash test, migration test
- [x] CI workflow (ruff, mypy, pytest)
- [x] Commit

Verified: 2026-11-01. 23 tests passing; ruff and mypy clean; live smoke runs of the Hacker
News and USAspending collectors.

## Phase 2: entity resolution, features, scoring, explainability

- [x] Deterministic, fuzzy, and LLM-ready entity resolution; review queue (`radar review`)
- [x] 100 hand-labeled entity pairs; precision gate (moved to Phase 4: needs the full collection)
- [x] Point-in-time feature builder (`as_of`) for all six feature groups, with evidence
- [x] Thesis classifier (embeddings plus cues, 117 labels); gate measured in Phase 4 with the LLM judge
- [x] Percentile normalization within sector and date; weighted score; `explain.py`
- [x] Feedback table and `make refit` writing `docs/weights-history.md`
- [x] Two-stage enrichment under a request budget; `radar score` end to end
- [x] Commit

Verified: 2026-11-01. 37 tests passing; ruff and mypy clean; live end-to-end run on six
months of data (3,601 companies, 600 scored in 517 seconds).

## Phase 3: agents, tracing, cost cap, prompts, verifier

- [x] `agents/base.py`: pydantic output, one retry, heuristic fallback, cost check, trace
- [x] Spend cap ($15) with `cost_log` and a durable ledger; prices from Anthropic's published rates
- [x] Sourcing (Haiku), Research (Sonnet), Verifier (Sonnet, isolated), Digest (Haiku), entity and thesis judges
- [x] Versioned prompts in `radar/agents/prompts/`
- [x] Wired into the live pipeline (`radar score`, `radar memo`)
- [x] Commit

Verified: 2026-11-01. 48 tests passing; ruff and mypy clean; live memo with 18 of 20 facts
supported on first draft; spend to date $0.063.

## Phase 4: backtest and eval gates (stop and review with Mohamed)

- [x] `eval/backtest/rounds.csv` with sourced announcement dates; exclusions with reasons
- [x] Multi-year collection (resumable, checkpointed); 100 hand-labeled entity pairs from it
- [x] Name-free universe per window from thesis queries; query audit log (passed)
- [x] Features at T minus 30 and T minus 90; coverage, rank, recall@25, recall@100, lead time
- [x] Baseline and lift (see BUILD_CONTRACT D5)
- [x] Second backtest requested by Mohamed: Form D outcomes for the top 100 against random and stars, with a like-for-like control
- [x] `docs/backtest-report.md` with limitations
- [x] All eval gates computed (claim citation precision waits on Mohamed's 40-claim spot-check)
- [x] **Stop. Show Mohamed the results.** Corrective run approved (thesis labels v2, entity resolver v2), applied, and logged
- [x] Commit

Verified: 2026-11-03. 61 tests passing; ruff and mypy clean; corrective run fingerprint
b5c9630306afa959. Portfolio test 0 of 12; Form D lift like for like 1.65x at 6 months and 1.13x
at 12 months, both intervals include 1. Gates: thesis 0.900, entity 0.984, leakage pass, spend
$0.31; claims pending.

## Phase 5: dashboard, deploy, cron, digest, audio, docs, README

- [x] Next.js dashboard (eight pages), access code middleware, disclaimer footer (local; verified 2026-11-03)
- [ ] Vercel deploy with Neon Postgres
- [ ] GitHub Actions weekly cron (Monday 13:00 UTC)
- [ ] Resend digest and ElevenLabs audio briefing
- [ ] Screenshots (phone and desktop: done 2026-11-03), docs 00 to 09, architecture diagram
- [x] Portfolio Stats data and LP update draft (`portfolio/`)
- [ ] Private signals intake (warm intros and notes) and Live Picks
- [ ] Offline `make demo`
- [ ] README; push public

## Phase 6: application documents

- [ ] Send Mohamed the Part 3 claims ledger in one message and wait
- [ ] Resume (one page), Builder Dossier (up to four pages), cover letter (one page), PDF and DOCX

## Phase 7: demo script, shot list, outreach pack, interview prep

- [ ] `docs/demo-script.md`, `docs/shot-list.md`
- [ ] `application/outreach.md`, `application/interview-prep.md`
- [ ] Final QA checklist
