# TASK: Grade a pending NBA prediction against game results

You verify whether a single `game` or `series` prediction came true, using **only** the NBA
results you are given. The deterministic work is already done for you: a fact bundle scoped
to this prediction's teams, and the prediction to grade, are passed in your instructions.
Make the judgement call, write it back to the prediction's source file, then stop.

## Inputs (passed in your instructions)

- **"Facts (scoped to this prediction's teams):"** — JSON with three keys:
  - `series` — derived playoff series: `teams`, `winner`, `loser`, `length` (number of
    games), `result` (e.g. `"PHI beat BOS 4-3"`), and per-game `games` with `game_no`,
    `winner`, `score`, and `series_after` (the running series record after that game).
  - `playin` — single play-in games: `date`, `winner`, `loser`, `score`.
  - `games` — raw games involving the relevant teams (`date`, `winner`, `loser`, `season_type`).

  These facts are pre-filtered to the teams named in this prediction — treat them as the
  complete relevant record. If the specific series or game needed to decide the claim is
  genuinely absent (not yet played or not in the cache), return `undetermined`.

- **"Prediction to grade:"** — one TSV row: `prediction_id`, `video_id`, `category`,
  `prediction_text`. Currently `status='pending'`.

Team names in the facts are 3-letter abbreviations (e.g. `BOS`, `PHI`, `GSW`). To map a
team name in the prediction text to its abbreviation, use the canonical codes in
`docs/team-abbreviations.md` verbatim — never guess an abbreviation. Use those same codes
in any `grade_note` you write, so they always match the facts.

## What to do for this prediction

1. **Decide the outcome from the facts:**
   - `correct` — the claim is fully borne out by the facts.
   - `incorrect` — the facts contradict any part of the claim. Compound claims must be
     true in full (e.g. "win Game 5 to tie the series 2-2" is `incorrect` if they won
     Game 5 but the series became 3-2, not 2-2).
   - `undetermined` — the outcome is not decidable from the facts (the series/game isn't
     in the data yet, or the claim isn't expressible from results). **Do not guess.**
2. **Find the source file:** the prediction lives in the `predictions.json` whose episode
   folder name contains its `video_id` (under `podcasts/`). Locate that file and the
   object whose `prediction_id` matches.
3. **Edit that object in place:**
   - Set `"status"` to `correct` / `incorrect` / `undetermined`.
   - Set `"grade_note"` to one sentence justifying the call against the facts, citing the
     concrete result (e.g. `"PHI beat BOS 4-3, so the Celtics did not win the series."`).
   Change nothing else in the file — leave other predictions and fields untouched. If a
   prediction object has no `grade_note` key yet, add it.

Do not touch `predictions.db`, do not run sync, do not commit — the shell does that.
Grade this prediction, then stop.
