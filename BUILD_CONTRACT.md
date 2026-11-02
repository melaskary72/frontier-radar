# Build Contract

Locks what Frontier Radar is, where its data comes from, and what it will not do. Changes to
this file are recorded in `docs/09-change-log.md`.

Frontier Radar is an independent work sample by Mohamed El Askary, built for the Anti Fund MTS (Member of Technical Staff) / Associate application. Not affiliated with Anti Fund.

## 1. Purpose

An agentic sourcing system that finds technical founders in robotics, defense, energy,
space, and manufacturing before consensus, explains every ranking with cited evidence, and
proves itself with a point-in-time backtest on Anti Fund's public portfolio.

## 2. Canonical sources

Public, documented endpoints only. Every response is cached in `raw_snapshots` with
`fetched_at` and a SHA-256 content hash. Rate limits live in `config/sources.yaml`.

| Source | Endpoint | Used for | Point-in-time? |
|---|---|---|---|
| GitHub REST + GraphQL | api.github.com | repo discovery, forks, issues, pull requests, releases, commits, owners, batched metadata | yes (event timestamps) |
| GitHub Events API | api.github.com/repos/{r}/events | recent stars (WatchEvent, last 90 days) | live radar only |
| arXiv API | export.arxiv.org/api/query | papers by category and keyword | yes (submittedDate) |
| Semantic Scholar Graph API | api.semanticscholar.org/graph/v1 | citations, h-index, co-authors | partial (counts are current) |
| OpenAlex | api.openalex.org | dated affiliation history per author | yes (publication dates) |
| Hacker News (Algolia) | hn.algolia.com/api/v1 | Show HN and Launch HN posts | yes (created_at) |
| Greenhouse, Lever, Ashby | public job-board APIs | open roles, engineering and hardware hiring | no (live only) |
| SEC EDGAR | efts.sec.gov, www.sec.gov/Archives | Form D filings | yes (filing date) |
| USAspending.gov | api.usaspending.gov/api/v2 | DoD, DOE, NASA awards including SBIR and STTR | yes (award start date) |
| SBIR.gov | api.www.sbir.gov | SBIR awards | unavailable (see D2) |
| Tranco | tranco-list.eu/api | domain rank history | yes (list date) |

Optional and disabled without keys: X (Twitter) API, Product Hunt API.

## 3. Scope

- Collectors for every source above, each with rate limiting, retry with backoff, caching,
  a recorded-fixture unit test, and a `--since` flag.
- Entity resolution (deterministic, fuzzy, then LLM (large language model) for ambiguous
  pairs, with a human review queue).
- Explainable scoring with weights in `config/weights.yaml`.
- Four agents (Sourcing, Research, Verifier, Digest) with schema-validated output, tracing,
  and a hard spend cap of $15 for the whole build.
- A point-in-time backtest on the public portfolio with a name-free candidate universe.
- Portfolio Stats from public proxies, clearly labeled as not Anti Fund's marks.
- A Next.js dashboard, weekly email digest, and audio briefing.

## 4. Out of scope

- LinkedIn or any scraping behind a login, paywalled content, or private data.
- Anti Fund's logo, mascot, or wordmark.
- Any code, data, prompts, or documents from the author's employer, and anything related to
  fraud or risk decisioning.
- Automatic weight changes: `make refit` proposes, a person decides.
- Claims of Anti Fund's real marks, metrics, or deal flow.

## 5. Rules

1. Leakage: no portfolio company name or domain in `radar/` or `config/`. Enforced by
   `tests/test_leakage.py` in CI. Names live only in `eval/`. Three portfolio companies
   (OpenAI, SpaceX, Anduril) are allowlisted only inside the `top_labs` block of
   `config/sources.yaml`, because people leaving them is a founder signal.
2. Every memo claim cites a snapshot; unsupported claims never reach a published memo.
3. Failed gates stay visible in the README with cause and planned fix.
4. No em dashes (U+2014) in any file: `tests/test_style.py` and `make check-dashes`.
5. Secrets only in `.env`; `.env.example` is committed.

## 6. Deviations from the original brief (decided during the build)

| ID | Date | Brief said | What changed | Why |
|---|---|---|---|---|
| D1 | 2026-11-01 | Star velocity from GitHub `starred_at` | Point-in-time momentum uses fork, issue, pull request, release, and commit timestamps. Live radar adds recent stars from the Events API (last 90 days). | GitHub's REST stargazers endpoint returns 404 and the GraphQL stargazers connection returns zero edges for repositories the token does not own (verified on ros2/ros2, torvalds/linux, huggingface/transformers, PX4/PX4-Autopilot). Total counts are still visible; historical timelines are not. GH Archive has the events but is about 19 MB per hour compressed, which is impractical without BigQuery. |
| D2 | 2026-11-01 | SBIR.gov awards API | SBIR and STTR awards come from USAspending (keyword and description match); the SBIR.gov collector records the 403 and degrades to no rows. | Every api.www.sbir.gov endpoint, including solicitations, returns 403 Forbidden. |
| D3 | 2026-11-01 | Affiliation history from Semantic Scholar | OpenAlex authorships supply dated affiliations; Semantic Scholar still supplies citations, h-index, and co-authors. | Semantic Scholar's public API exposes current affiliations only, which cannot answer "left a top lab in the last 18 months". |
| D4 | 2026-11-01 | `grep -rnP` em dash check | `make check-dashes` and a pytest. | macOS grep has no `-P`. Same check, portable. |
| D5 | 2026-11-01 | Stars baseline in the backtest | To be settled in Phase 4: stars at T are not observable (D1). | Logged here so the baseline choice is explicit before results exist. |
