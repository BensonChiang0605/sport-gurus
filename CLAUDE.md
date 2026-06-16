# CLAUDE.md

Guidance for working in this repo.

## What this project does

Analyses how accurate sports podcasts are at predicting outcomes — both as a leaderboard (which podcast is most accurate) and over time (one podcast's track record). The pipeline ingests YouTube episodes, has an LLM extract verifiable predictions, and aggregates them into a queryable database for grading.

## Data layout

```
podcasts/
  <sport>/                              # e.g. basketball
    <podcast>/                          # e.g. draymond-green-show
      episodes/
        <YYYY-MM-DD>_<video_id>/        # one folder per episode; sorts chronologically
          transcript.txt                # ingested: combined transcript, timestamps stripped
          meta.json                     # ingested: video_id, title, url, episode_date, duration, podcast, sport
          summary.md                    # generated: human-readable summary + predictions w/ reasoning
          predictions.json              # generated: structured predictions (source of truth for the DB)
game-data/
  nba-games.json                        # committed cache of NBA results (refreshable; grading source)
scripts/
  ingest_channel.py                     # fetch transcripts + upload dates -> cleaned episodes/
  nba_games.py                          # NBA results: fetch/cache + game & series query helpers + grading facts
ralph/
  prompt.md                             # the per-episode summarisation task
  once.sh                               # process ONE episode (interactive or specified)
  afk.sh                                # batch loop: process many episodes unattended
  sync.py                               # build predictions.db from all predictions.json files
  grade-prompt.md                       # the grading task (resolve pending game/series predictions)
  grade.sh                              # grade pending game/series predictions against NBA results
predictions.db                          # SQLite, gitignored, DERIVED — never edit by hand
```

The episode folders are the **source of truth**. `predictions.db` is derived and
gitignored; rebuild it anytime with `python3 ralph/sync.py`.

## The Ralph summarisation workflow

"Ralph" is a loop that summarises episodes one at a time. The key principle:
**deterministic work lives in shell/Python; only judgement lives in the LLM.**

### Division of labour

- **Shell (`once.sh` / `afk.sh`)** — picks which episode to process, passes its path
  to Claude, then runs `sync.py`, `git add -A`, and `git commit`. The LLM never
  picks episodes, never touches the database, and never commits.
- **LLM (driven by `prompt.md`)** — reads `transcript.txt` + `meta.json` for the one
  episode it was handed, then writes exactly two files into that episode folder:
  `summary.md` (human-readable, with reasoning sub-bullets per prediction) and
  `predictions.json` (structured rows). That's it.
- **`sync.py`** — scans every `predictions.json` under `podcasts/` and upserts into
  `predictions.db` (`INSERT OR REPLACE` keyed on `prediction_id`, so it's idempotent).
  It also migrates legacy `predictions.csv` files for episodes that predate the JSON
  workflow.

### Episode selection

The shell finds the oldest episode that has a `transcript.txt` but no `summary.md`.
Because folders are named `<YYYY-MM-DD>_<video_id>`, lexical sort = chronological order.

### Commands

```bash
# Process ONE episode (auto-picks oldest unsummarised in the podcast folder)
./ralph/once.sh podcasts/basketball/draymond-green-show

# Process ONE specific episode
./ralph/once.sh podcasts/basketball/draymond-green-show 2026-05-10_someVideoId

# Batch: up to N iterations, one episode each, stops when none remain
./ralph/afk.sh 30 podcasts/basketball/draymond-green-show

# Batch within an inclusive episode-name range (start and/or end optional)
./ralph/afk.sh 30 podcasts/basketball/draymond-green-show 2026-05-10_a 2026-05-20_z

# Rebuild the database manually (e.g. if a run was interrupted before sync)
python3 ralph/sync.py
```

`afk.sh` runs Claude headless (`--print --output-format stream-json`); `once.sh` runs
it interactively. Both pin `--model claude-sonnet-4-6`.

### predictions.json schema

One object per **objectively verifiable** prediction (drop subjective takes). Keys:

`prediction_id` (`<video_id>-NN`), `podcast`, `video_id`, `episode_date`, `speaker`,
`prediction_text`, `category` (`game|series|award|season`), `verifiable`
(`auto` = checkable from NBA game records · `manual` = objective but needs another
source), `status` (always `pending` at write time), `argument` (the speaker's stated
rationale, mirrored from the `summary.md` reason bullets), `grade_note` (empty at write
time; filled by the grading pass with a one-line justification of the verdict). If an
episode has no verifiable predictions, write `[]`.

## The grading workflow

Resolves `pending` → `correct`/`incorrect` for `game`/`series` predictions. Same split as
Ralph: **deterministic work in Python, only the true/false judgement in the LLM.**

- **`scripts/nba_games.py`** is the deterministic game-data layer. It fetches results
  from `nba_api`, caches them in `game-data/nba-games.json` (committed, diffable,
  refreshable), and derives facts. It's game-list-first and season-type-agnostic — every
  game is tagged `regular`/`playin`/`playoff`. Series are a *derived view* over playoff
  games only (regular season has no series); use the game-level helpers
  (`games_between`, `result_on`, `season_series`) for individual/regular-season claims and
  the series-level helpers (`did_win_series`, `series_length`, `game_by_number`) for
  playoff series.
- **`grade.sh`** (deterministic orchestration) refreshes the cache, builds the fact
  bundle (`--facts`), pulls the pending `auto` `game`/`series` rows from `predictions.db`,
  hands facts + rows to Claude headless, then runs `sync.py`, `git add -A`, `git commit`.
- **The LLM (driven by `grade-prompt.md`)** judges each prediction against the facts only,
  sets `status` to `correct`/`incorrect`/`undetermined` (never guesses), writes a one-line
  `grade_note`, and edits the matching `predictions.json` object in place. It never touches
  the DB or commits.

```bash
# Grade all pending game/series predictions against current NBA results
./ralph/grade.sh
```

Adding a season type later (e.g. regular season) is just `--season-types "Regular Season"`
on `nba_games.py`; no other changes needed.

## Conventions & gotchas

- **Never hand-edit `predictions.db`** — it's regenerated from `predictions.json`.
- **Editing a prediction** means editing that episode's `predictions.json`, then
  re-running `sync.py`.
- If a Ralph run is interrupted after the LLM writes its files but before the shell
  syncs/commits, just run `python3 ralph/sync.py` and commit — the files are already
  on disk.
- `status` grading (resolving `pending` → `correct`/`incorrect`) is a separate pass,
  not part of summarisation — see the grading workflow below.

## Other commands

```bash
# Ingest a podcast's episodes from YouTube
uv run scripts/ingest_channel.py \
  --channel DraymondGreenShow --sport basketball --podcast draymond-green-show --max 5

# Look up NBA results (default: postseason; prints the game list)
uv run scripts/nba_games.py --season 2025-26

# Refresh the committed games cache
uv run scripts/nba_games.py --write
```
