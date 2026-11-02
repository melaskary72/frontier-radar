# Change Log

Newest first. Every corrective run, deviation, and gate change is recorded here.

## 2026-11-01: Phase 3

- Agent base: JSON-schema output validated with pydantic plus semantic checks, one retry
  that shows the model its error, deterministic fallback on a second failure, refusal,
  spend cap, or missing key; one `agent_runs` row per run with prompt version, tokens,
  cost, latency, and status. Em dashes are stripped from model output.
- Spend cap: worst-case check before every call; actual cost to `cost_log` and to an
  append-only ledger (`data/cost_ledger.jsonl`) so rebuilding the database cannot reset it.
- Agents: Sourcing (Haiku 5.5), Research (Sonnet 5.5), Verifier (Sonnet 5.5, isolated),
  Digest (Haiku 5.5), plus entity and thesis judges (Haiku 5.5). Versioned prompts in
  `radar/agents/prompts/`. Sonnet 5.5 calls use the server-side refusal fallback
  (`fallbacks: "default"`).
- Evidence packs cut each citation's excerpt from the raw snapshot, not derived tables.
- Bug caught by tests before any live run: `strict_schema` dropped every field name inside
  `properties`, which would have sent empty schemas.
- Live check (as of 2026-11-01): four why-now lines (all `ok`, $0.0017 total) and one
  full memo for a robotics vision repository. Verifier: 18 of 20 facts supported on the
  first draft, 2 partial claims rewritten to match the source, 0 unsupported published.
  Spend to date $0.063 including a 22-token setup check.

## 2026-11-01: Phase 2

- Entity resolution in three tiers (deterministic, fuzzy, LLM or review queue) with human
  decisions as hard constraints; stale companies retired after merges. Migration `0002`.
- Founder graph: papers link to code through GitHub URLs in arXiv abstracts (8% of robotics
  papers in the dev sample); authors link to companies through those repositories.
- Point-in-time features with evidence; percentile scoring within sector and date;
  explanations with the top three evidence items; feedback and `radar refit`.
- Thesis classifier, 117 hand-labeled items (rubric in eval/labels/README.md), stratified
  5-fold cross-validation, three seeds: TF-IDF 62%; bge-small 66 to 68%; bge-base 67 to
  73%; bge-small plus rubric cues 71 to 74% (shipped). Below the 85% gate; see D6.
- Live end-to-end run on six months of data (3,601 companies, 2,079 in thesis, 60 enriched,
  600 scored in 517 seconds). Fixes from reading the output: generic title terms ("grid",
  "chip", "orbit") removed; thesis gate at 0.5; research groups and incumbents left out of
  the default ranking (D7); domains claimed by two GitHub owners treated as generic.
- arXiv returned 429 at 3.1 second spacing; spacing raised to 4 seconds and each sector now
  commits on its own, so one rate-limited sector no longer rolls back the rest.
- Entity-pair labels (precision gate) move to Phase 4: six months of data produced only 47
  cross-source candidate pairs.

## 2026-11-01: Phase 1

- Scaffolded the repository, configs, schema (Alembic `0001`), snapshot cache, and ten
  collectors with recorded real fixtures.
- Finding: GitHub no longer returns stargazer lists or `starred_at` for repositories the
  token does not own (REST 404, GraphQL zero edges). Momentum moved to fork, issue, pull
  request, release, and commit timestamps; recent stars from the Events API feed the live
  radar only. BUILD_CONTRACT deviation D1.
- Finding: SBIR.gov API returns 403 on every endpoint. SBIR and STTR awards now come from
  USAspending. Deviation D2.
- Added OpenAlex for dated affiliation history (Semantic Scholar exposes current
  affiliations only). Deviation D3.
- Hacker News: Algolia's loose matching returned off-thesis posts. Search is restricted to
  titles with typo tolerance off, and titles must contain a sector `title_terms` entry as a
  whole word.
- SEC EDGAR requires a contact address in the User-Agent; it is read from `SEC_USER_AGENT`
  in `.env` and never written to fixtures.
