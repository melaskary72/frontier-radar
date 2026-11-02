# Change Log

Newest first. Every corrective run, deviation, and gate change is recorded here.

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
