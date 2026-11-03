# Portfolio Stats data

Public proxies for Anti Fund's portfolio, for the dashboard's Portfolio Stats page. Public data
only. These are not Anti Fund's actual marks or metrics.

## Scope

Tracked: the 34 active companies in antifund.com's Headline; AI, Infrastructure and National
Resilience; and Software, Finance and Applied AI lists. Not tracked: the Consumer list (consumer
brands have no technical or hiring footprint these proxies measure well) and Exits (no longer
portfolio companies).

Portfolio names live only in this folder and under `eval/`. `tests/test_leakage.py` fails if any
of them appears in `radar/` or `config/`, so the radar can never look a portfolio company up by
name.

## Files

- `companies.csv`: the seed. Every domain comes from the link antifund.com's portfolio section
  gives for that company (`domain_source_url`). GitHub organizations are used only when the
  organization's profile lists the same domain. ATS boards (applicant tracking system:
  Greenhouse, Lever, Ashby) are used only when the company's own site links to that board.
  Anything that could not be verified is left blank, with the reason in `notes`.
- `press_rounds.csv`: hand-curated rounds, each with a public press source. Reuses the rounds
  sourced for the backtest (`eval/backtest/rounds.csv`), excluding dates that come only from
  data aggregators.
- `collect.py`: seeds `portfolio_companies` and writes `portfolio_facts` (open roles, GitHub
  stars and recent activity, Tranco rank, Form D filings, press rounds) into the live database.
  Every fact is fetched through the radar's cached, rate-limited collectors, so it has a raw
  snapshot with its fetch time and SHA-256. Reruns on the same day replace that day's facts.
- `lp_update.py`: writes `web/public/data/lp_update.json`, a quarterly-style update draft built
  by template from the tracked facts. No LLM. Every sentence lists the facts and source URLs it
  rests on.

Run:

    DATABASE_URL=sqlite:///data/live.db uv run python portfolio/collect.py
    DATABASE_URL=sqlite:///data/live.db uv run python portfolio/lp_update.py
