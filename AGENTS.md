## What this project does

Analyses how accurate sports podcasts are at predicting outcomes — both as a leaderboard (which podcast is most accurate) and over time (one podcast's track record). The pipeline ingests YouTube episodes, has an LLM extract verifiable predictions, and aggregates them into a queryable database for grading.

All predications are saved in `predictions.db`

## The Ralph summarisation workflow

"Ralph" is a loop that summarises episodes one at a time. The key principle:
**deterministic work lives in shell/Python; only judgement lives in the LLM.**

## Conventions & gotchas

- **Team abbreviations are canonical** — every LLM must refer to a team by the exact 3-letter code in [docs/team-abbreviations.md](docs/team-abbreviations.md), never an ad-hoc abbreviation.
- **Never hand-edit `predictions.db`** — it's regenerated from `predictions.json`.
- **Editing a prediction** means editing that episode's `predictions.json`, then
  re-running `sync.py`.
- If a Ralph run is interrupted after the LLM writes its files but before the shell syncs/commits, just run `python3 ralph/sync.py` and commit — the files are already on disk.