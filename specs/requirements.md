# Requirements

Verified: 2026-11-01 (Phase 1). Each requirement has an ID that acceptance criteria and tests
reference.

## Users

- **Partner on a phone** between meetings: Radar, Company, and the audio digest.
- **Associate at a desk**: Pipeline, Agent Ops, Backtest, Portfolio Stats, Review Queue.

## Functional

| ID | Requirement | Phase |
|---|---|---|
| R1 | Collect public signals for the six thesis sectors from GitHub, arXiv, Semantic Scholar, OpenAlex, Hacker News, job boards, SEC Form D, USAspending, SBIR.gov, and Tranco. | 1 |
| R2 | Cache every raw response with `fetched_at` and SHA-256; every derived row references its snapshot. | 1 |
| R3 | Each collector supports `--since`, rate limiting, retry with backoff, and an offline mode. | 1 |
| R4 | Resolve people and companies across sources: deterministic, then fuzzy, then LLM (large language model) for ambiguous pairs; below 0.8 confidence goes to a review queue. | 2 |
| R5 | Score companies with six weighted feature groups; explain each score with per-feature contributions and the top three evidence items. | 2 |
| R6 | Record thumbs up or down per company; `make refit` proposes new weights without applying them. | 2 |
| R7 | Sourcing, Research, Verifier, and Digest agents with schema-validated output, one retry, heuristic fallback, cost check before every call, and a full trace. | 3 |
| R8 | Every factual memo sentence cites a snapshot; the Verifier reads only the cited text and removes unsupported claims. | 3 |
| R9 | Hard LLM spend cap of $15 for the build, enforced in code with a `cost_log`. | 3 |
| R10 | Point-in-time backtest at T minus 30 and T minus 90 days on the primary and secondary portfolio sets, with a name-free universe and a baseline. | 4 |
| R11 | CI fails if any portfolio name or domain appears in `radar/` or `config/`. | 1 |
| R12 | Portfolio Stats from public proxies with an LP (limited partner) update draft where every sentence is cited. | 5 |
| R13 | Dashboard pages: Radar, Company, Pipeline, Backtest, Agent Ops, Portfolio Stats, Review Queue, About; access code; disclaimer footer. | 5 |
| R14 | Weekly pipeline every Monday 13:00 UTC; email digest and an audio briefing. | 5 |

## Non-functional

| ID | Requirement |
|---|---|
| N1 | `make demo` works from a fresh clone with no keys in under 2 minutes. |
| N2 | Weekly pipeline finishes in under 20 minutes. |
| N3 | Dashboard scores 90+ on Lighthouse accessibility (mobile). |
| N4 | No em dashes in any file. |
| N5 | Public data only; documented endpoints; terms of service and rate limits respected. |
