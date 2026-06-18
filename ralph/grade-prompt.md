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
   This is the **exact** grade — the full claim exactly as stated, including any game count.
2. **For `series` predictions, also decide the general grade.** A series prediction often
   carries a less-specific sub-claim: *which team wins the series*, ignoring the game count.
   Grade that separately:
   - If the prediction **specifies a game count** (e.g. "in 5 games", "in six", "goes the
     distance to 7"), grade the general claim — *did the named team win the series at all?* —
     independently of the count. So "BOS win vs PHI in 5" is exact-`incorrect` but
     general-`correct` if BOS won the series in 6.
   - If the prediction **specifies no game count** (e.g. "BOS win vs PHI"), it is already
     general: set the general grade equal to the exact grade.
   - If the series is not complete or not in the facts, **both** the exact and general
     grades are `undetermined`.
   This whole step is **`series`-only**. For a `game` prediction, do not produce a general
   grade — leave the general fields out.
3. **Find the source file:** the prediction lives in the `predictions.json` whose episode
   folder name contains its `video_id` (under `podcasts/`). Locate that file and the
   object whose `prediction_id` matches.
4. **Edit that object in place:**
   - Set `"status"` to the exact grade: `correct` / `incorrect` / `undetermined`.
   - Set `"grade_note"` to one sentence justifying the exact call against the facts, citing
     the concrete result (e.g. `"PHI beat BOS 4-3, so the Celtics did not win the series."`).
   - **For `series` predictions only**, also set:
     - `"status_general"` to the general grade: `correct` / `incorrect` / `undetermined`.
     - `"grade_note_general"` to one sentence justifying the general call (e.g.
       `"BOS beat PHI 4-2, so the Celtics did win the series even though not in 5."`). When
       the prediction had no game count, mirror `grade_note` here.
   Change nothing else in the file — leave other predictions and fields untouched. If a
   prediction object is missing the `grade_note`, `status_general`, or `grade_note_general`
   keys, add the ones that apply.

The market benchmark fields (`market_prob`, `market_favorite`, `market_source`) are filled
by a separate pass (`ralph/fill-odds.sh`) — leave them untouched here.

Do not touch `predictions.db`, do not run sync, do not commit — the shell does that.
Grade this prediction, then stop.
