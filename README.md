# sport-gurus

Analyse how accurate sports podcasts are at predicting outcomes — both as a
leaderboard (which podcast is most accurate) and over time (one podcast's track record).

## Layout

```
podcasts/
  <sport>/                          # e.g. basketball
    predictions.csv                 # canonical ledger for ALL podcasts in this sport
    <podcast>/                       # e.g. draymond-green-show
      episodes/
        <YYYY-MM-DD>_<video_id>/     # one folder per episode, sorts chronologically
          transcript.txt            # combined transcript, timestamps stripped
          meta.json                 # video_id, title, url, episode_date, episode_datetime, duration
          summary.md                # generated: summary + verifiable predictions
scripts/
  ingest_channel.py                 # fetch transcripts + dates -> cleaned episodes/
  nba_games.py                      # reusable NBA game-result lookups (nba_api)
ralph/
  prompt.md                         # default summarisation prompt (one episode/run)
  afk.sh                            # batch loop driver
  once.sh                           # single interactive run
```

One `predictions.csv` **per sport** (not per podcast, not global): we compare podcasts
within a sport, and a cross-sport roll-up is a trivial concat if ever needed. Only
**objectively verifiable** predictions are logged — subjective takes are dropped.

### `predictions.csv` columns

`prediction_id, podcast, video_id, episode_date, episode_datetime, speaker, prediction_text, category,
verifiable, resolve_after, status, evidence`

- `category`: `game | series | award | season`
- `verifiable`: `auto` (checkable from NBA game records) · `manual` (objective, other source)
- `status`: `pending | correct | incorrect | partial` (grading is a separate pass)

## Workflows

### Ingest a podcast's episodes

```bash
uv run scripts/ingest_channel.py \
  --channel DraymondGreenShow --sport basketball --podcast draymond-green-show --max 5
```

Pulls transcripts via `ytfetcher` and upload dates via the `yt-fetch` skill (yt-dlp),
joins on `video_id`, strips timestamps, and writes one cleaned episode folder each.
Dates are cached in `.video_meta.json` so re-runs don't re-hammer YouTube; runs are
idempotent (existing episodes are skipped). Pass `--from-json <dump>` to ingest from an
existing `ytfetcher` JSON instead of fetching live.

### Batch-summarise episodes

```bash
./ralph/afk.sh 30          # summarise up to 30 episodes, one per iteration
```

Each iteration picks the oldest episode lacking a `summary.md`, writes its summary,
appends its verifiable predictions to the sport's `predictions.csv`, and commits. The
loop stops when no un-summarised episodes remain. `./ralph/once.sh` runs a single
episode interactively.

### Check NBA results (for grading predictions)

```bash
uv run scripts/nba_games.py --season 2025-26
```

## Out of scope (planned next)

- **Grading**: resolve `pending` predictions against `scripts/nba_games.py`, set
  `status` + `evidence`.
- **Leaderboard**: aggregate accuracy per podcast and over time from the graded ledger.
