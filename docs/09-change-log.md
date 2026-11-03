# Change Log

Newest first. Every corrective run, deviation, and gate change is recorded here.

## 2026-11-03: Claim gate closed; memo quality (research.v2 and v3)

- Claim citation gate: Mohamed marked all 40 sampled published claims supported (1.0 against
  a 0.95 target). He found one duplicate (claims 8 and 25 state the same creation date). The
  report now says what this gate measures: factual citation accuracy on mostly metadata
  claims, not the quality of a memo's analysis.
- Memo publish rules: repeated sentences are dropped before verification, including a fact
  whose words are already contained in an earlier fact citing the same sources. Analysis
  sentences carrying numbers that appear in no evidence item are dropped. Both counted in the
  memo's verifier summary. Tests in `tests/test_agents.py`.
- Prompt research.v2: sections Snapshot, Team, Technology, Why now, Market, Signals timeline,
  Risks, Open questions. Technology must name the hard technical problem and what is novel or
  ordinary; Why now must say what changed recently; Risks must give specific technical,
  commercial, and competition or team failure modes. The structure check rejects drafts with
  fewer than 2, 2, and 3 substantive analysis sentences in those sections.
- Prompt research.v3: the first v2 memo (Robonine) lost its Team section because the model
  stated "no founders are named" as a cited fact, which the verifier rightly removed. v3 asks
  for that as analysis. v2 is kept unchanged for traceability.
- Rendering: each section prints cited facts first, then a paragraph marked `_Analysis:_`; the
  dashboard shows analysis in a labeled gold block. Robonine memo v3: 11 facts checked, 10
  supported, 1 rewritten to match its source, 1 duplicate dropped. Spend to date $0.41.

## 2026-11-03: Corrective run results and the Form D outcome backtest

- Corrective run (labels v2, resolver v2), fingerprint `b5c9630306afa959`, 23 cutoffs, about
  2 hours. Portfolio result unchanged: 0 of 12 companies ever collected, query audit passed
  (593 thesis queries, none naming a target). The retrained classifier is more selective
  (for example 20,617 in-thesis companies at the 2026-05-10 cutoff against 32,572 in run 1).
- New second backtest, requested by Mohamed (`eval/backtest/formd_fetch.py`,
  `formd_lift.py`, `formd_results.json`, `formd_matches.csv`): of the radar's top 100 at each
  cutoff, the share that filed a new Form D within 6 and 12 months, against the in-thesis
  universe (the expected value of a random 100, plus one seeded random draw per cutoff) and
  the GitHub-stars top 100. Source: all 166,070 Form D filings since January 2024 from SEC
  bulk files (structured data sets through 2026 Q1, the EDGAR filing index after), cached with
  fetch time and SHA-256. Rules fixed before results: new filings only, complete windows only
  (17 cutoffs at 6 months, 9 at 12), one exact-name matcher for every arm, 95% intervals from a
  bootstrap over companies.
- Result: raw lift 39.2x at 6 months (0.82% against 0.021%) and 23.1x at 12 months, but every
  hit comes from 3 companies and the intervals include 1. After seeing that the radar's top 100
  is mostly organizations and federal awardees while the universe is 92% individual accounts,
  a like-for-like control was added (each entry expected at its own entity type's in-thesis
  rate): 1.65x at 6 months (0 to 3.85) and 1.13x at 12 months (0 to 2.96). The stars baseline
  had no hits. Verdict, as reported: the Form D lift does not hold up, so it is not the
  headline. Sensitivities excluding prior Form D filers point the same way.
- Report (`docs/backtest-report.md`), analysis, and the dashboard Backtest page now show both
  tests, the Form D test first and the portfolio test second, with the verdict stated plainly.

## 2026-11-02: Phase 5 started in parallel, dashboard and Portfolio Stats

- New exporter (`radar/export.py`, `uv run radar export`) writes static JSON for the dashboard
  from the scored live run in `data/live.db` (as of 2026-11-01; copied from the Phase 3
  end-to-end database), agent runs from `data/radar.db`, and spend from
  `data/cost_ledger.jsonl`. Memo sentences map to verifier claims, and each citation carries
  the excerpt cut from the cached raw response with its fetch time and SHA-256 hash. All
  exported text passes through clean_text. Test: `tests/test_export.py`.
- Next.js dashboard in `web/`: Radar, Company (timeline, cited memo with verifier badges,
  evidence), Pipeline, Backtest (results pending), Agent Ops, Portfolio Stats, Review Queue,
  About. Design system per brief 5.13, dark mode, access code middleware enforced only when
  ACCESS_CODE is set. Votes, pipeline, and review decisions are stored in the browser until a
  shared backend is deployed. Screenshots of every page at phone and desktop sizes in
  `screenshots/`. Review fixes: the verifier chart axis is capped at 100%; Portfolio Stats
  reads the fact values and LP draft sections in the format the portfolio collector writes.
- Known data issue, left visible: the top-ranked company in the 2026-11-01 live run ("cvg")
  is an academic group that the lab filter missed; its own memo says the evidence shows no
  company. Planned fix: extend the academic markers before the next live run.
- Portfolio Stats data (`portfolio/`): the 34 active companies from antifund.com's Headline;
  AI, Infrastructure and National Resilience; and Software, Finance and Applied AI lists
  (consumer brands and exits not tracked). Domains come from antifund.com's own links. GitHub
  organizations and job boards are used only when the organization profile, the company site,
  or the postings confirm the company; same-name boards and organizations belonging to other
  companies were rejected and noted. 90 facts through the radar's cached, rate-limited
  collectors: open roles (24 companies), GitHub stars and recent pushes (12), Tranco rank
  (25), one company Form D (Saronic), Form D filings by investment vehicles named for 6
  companies (a secondary-demand proxy, labeled as such), and 10 press-reported rounds with
  sources. `portfolio/lp_update.py` builds an LP update draft by template, with no LLM; every
  sentence cites its facts and URLs. Public proxies only, not Anti Fund's actual marks.
- `make check-dashes` now skips Next.js build folders and checks `web/public/data` explicitly.

## 2026-11-02: Phase 4, first backtest result (labels v1, resolver v1)

- Run fingerprint `de781b69c5aec105`, 23 cutoffs, 55 minutes of compute after the fixes
  below. Query audit passed: 593 distinct thesis queries, none naming a target, every company
  traces to a discovery row.
- Result: 0 of 12 target companies appeared anywhere in the collection (211,064
  repositories, 662 launch posts, 959 papers, 1,712 awards), so coverage, recall at 25 and
  at 100, and the stars baseline are all zero. This is a coverage ceiling, not a ranking
  failure: per `eval/backtest/footprints.csv`, no included primary company had a public
  footprint the thesis queries could see before T minus 30 (Efference's first public
  repository came 6 days after its round; Trajectory's and Merge's first launch posts are on
  announcement day; Enigma's only pre-announcement trace is low-point blog links; General
  Galactic's account has no repositories). Pensive (secondary, applied AI) had public
  repositories from 2025-06-05, outside the thesis queries by design.
- Mohamed approved one corrective run with labels v2 and resolver v2 (below) before this
  result was in, to fix the gates. The corrective run cannot change coverage, which is decided before thesis and
  entity resolution; it makes the reported pipeline match the corrected gates.

## 2026-11-02: Thesis labels v2, classifier experiments, entity resolver v2

- Thesis adjudication: Mohamed decided all 22 items where Claude's v1 label and the Haiku
  judge disagreed (`eval/labels/thesis_disagreements.csv`): he sided with the judge on 12 and
  with Claude on 10. Optimism check: he also spot-checked a fixed-seed random 20 of the 95
  items where Claude and the judge agreed (`thesis_agreement_spotcheck.csv`) and flipped 1
  (a rocket nozzle CFD study, now in thesis): a 5% error rate on agreed items. Labels v2
  (`thesis_labels.csv`; v1 kept as `thesis_labels_v1.csv`) change 13 rows; 54 of 117 are in
  thesis. Every changed row records who changed it and why.
- Thesis gate on v2: 0.900 on the shipped path (classifier plus judge in the uncertain band;
  0.889 to 0.915 across seeds), judge alone 0.906, agreement between Claude's v1 labels and the
  judge 95 of 117 (81.2%). If the 5% agreed-item error rate holds for the 75 agreed items
  nobody reviewed, about 4 more errors exist and the true figure is near 0.87. The 5% rests on
  one flip in 20, so that correction is itself uncertain.
- Backtest thesis filter stays classifier-only by design (BUILD_CONTRACT D9): an LLM judge
  reading historical text could leak hindsight, because the model may already know which
  companies later became famous.
- Classifier experiments (`eval/thesis_experiments.py`, `eval/thesis_experiments.json`): six
  feature sets (embeddings, cue flags, label-free sector prototype similarities, and
  combinations) times two embedding models times five regularization strengths, fixed before
  the first run. Before (v1 shipped features on v2 labels): 0.681 plain CV. Best candidate on
  plain CV: 0.732 (optimistic, chosen on the same folds). Nested CV over the whole selection:
  0.687. Conclusion: no feature set reliably beats the baseline on 117 labels. No new regex
  cues were written after seeing errors.
- Distillation experiment (`eval/thesis_distill.py`, `eval/thesis_distill.json`): the Haiku
  judge labeled 1,000 live-radar company texts offline (portfolio names and domains and the
  117 test items excluded; about $0.07), the classifier trained on those, and all 117 human
  labels served as a held-out test set. Best result 0.709 (v1 features), against 0.681 for
  training on the 117 human labels: +2.8 points, inside the roughly 8-point 95% margin of a
  117-item test. The classifier matches the judge only about 75% of the time on its own
  training data, so a linear model on these embeddings is near its ceiling. Not adopted: the
  corrective run trains on the human v2 labels, as Mohamed specified, which also keeps
  LLM-derived labels out of the backtest's training data.
- Corrective backtest run launched with labels v2 and resolver v2. Run 1 results preserved as
  `eval/backtest/results_run1_labels_v1.json`.
- Bug found by the corrective run: when resolver v2 split a previously merged cluster, the new
  company row claimed a GitHub organization still held by the old row, and the unique
  constraint fired before stale rows were retired. The resolver now works out every
  cluster's final domain and GitHub organization first and releases identifiers held by any
  other row before inserting. Regression test in `tests/test_resolve.py` (fails with the
  original IntegrityError without the fix). The failed attempt rolled back in its first
  cutoff; the rerun was relaunched from scratch.
- Entity resolver v2: a fuzzy auto-merge now requires both names to keep a distinctive core
  after removing company suffixes, field words, and thesis vocabulary
  (`has_distinctive_core`). On the 100 labeled pairs it removes all 7 wrong fuzzy merges and
  keeps the one correct one (BlueHalo LLC and BlueHalo Labs). The rule was written after
  seeing those errors, so precision measured on the same pairs is in-sample; the live run has
  no fuzzy merges to use as a holdout. The rule only removes merges, so it cannot create new
  false merges. Regression test in `tests/test_resolve.py`.
- Entity gate rescored with resolver v2 (`eval/labels/rescore_entity_pairs.py`; labels
  unchanged, v1 decisions kept in `resolver_decision_v1`): exactly the 7 wrong fuzzy merges
  moved to the review queue and nothing else changed. Precision 0.984 (62 of 63 merges; the
  remaining one is labeled unsure), recall over pairs labeled same 0.939. In-sample, as noted
  above.

## 2026-11-02: Phase 4, collection interrupted and resumed

- The backtest collection stopped at 01:53 on `github/space` with
  `httpx.ConnectError: [Errno 8] nodename nor servname provided, or not known` (a DNS
  failure during a network outage). The old retry policy gave up after six attempts, about
  2.5 minutes. Not a code bug; 93 finished steps were already committed.
- Network errors are now retried per request with backoff capped at 5 minutes, for up to
  `RADAR_NETWORK_PATIENCE_S` seconds (default 900; the backtest collector uses 1800). HTTP
  errors keep the six-attempt limit.
- New `collection_checkpoints` table (Alembic `0003`). Every finished step (source, sector,
  date range) and every finished GitHub search window is committed with its rows, and a
  rerun skips them. The old `data/backtest_progress.json` was imported and removed.
- If the network stays down past the per-request limit, the collector pauses and retries
  the step from its last checkpoint, up to 8 times (about 6 hours of outage in total).
- Tests: `tests/test_resume.py` (retry through DNS errors, bounded patience, resume after a
  crash fetches only the unfinished window).
- Collection finished: 211,064 repositories, 662 Hacker News posts, 959 papers, 1,712
  awards; no GitHub search window left truncated.

## 2026-11-02: Phase 4, backtest run fixes

- Bug: the resolver looked up discovery rows for all 211,064 repositories in one `IN`
  clause, past SQLite's limit of 32,766 bound variables. The lookup is now chunked;
  regression test in `tests/test_resolve.py`.
- Bug: SQLite returns datetimes without an offset, so comparing a stored `last_commit_at`
  with an aware timestamp crashed enrichment on the second cutoff. All datetime columns now
  use a `UTCDateTime` type that writes UTC and reads back aware values (same DDL, no
  migration needed); test in `tests/test_resume.py`.
- The backtest saves each finished cutoff to `data/backtest_cutoffs/` with a fingerprint of
  the code, config, prompts, and thesis labels, and reuses it only when the fingerprint
  matches. `results.json` records the fingerprint. The pipeline now logs seconds per stage.
- Performance: the run finished 15 of 23 cutoffs, then spent over 90 minutes on the 16th.
  A Python stack dump showed it in `company_text`: the paper-link query filtered edges on
  `relation` and `src_id` without `src_type`, so SQLite scanned all 415,085 edges once per
  company (about 56,000 times per cutoff). Added `src_type == "repos"` in
  `radar/scoring/thesis.py` and `radar/enrich.py`; every `implements` edge starts at a
  repository, so results are unchanged, and the query now uses `ix_edges_src`. The new
  fingerprint means all 23 cutoffs are recomputed under one code version.
- Entity resolution gate labeled: 100 pairs (all 88 resolver decisions plus 12 declined
  pairs), rubric in `eval/labels/README.md`. Precision 0.886 against a 0.90 target: domain
  and GitHub-login merges 60 of 60 correct, fuzzy merges 1 of 8. Cause: name normalization
  strips words such as "Technologies", "Systems", and "Group" before the fuzzy comparison,
  while the distinctiveness check reads the unnormalized name. Gate left failing pending
  Mohamed's decision on a corrective run.

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
