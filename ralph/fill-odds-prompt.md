# TASK: Attach the market benchmark (Polymarket odds) to one NBA prediction

You route an already-computed prediction-market probability onto a single `game` or
`series` prediction's source file. This is **not** grading — you do not judge whether the
prediction came true, and you must not touch any grade. The only judgement here is reading
which team the prediction backs, so you can copy the number for that side.

## Inputs (passed in your instructions)

- **"Market odds (Polymarket, pregame):"** — JSON with the prediction-market-implied
  probability *at the start of the game/series*, already fetched for you (deterministic).
  Shapes:
  - `game` → `{probs: {ABBR: p, ABBR: p}, favored, source_slug, ...}`.
  - `series` → `{winner: {probs: {ABBR: p, ...}, favored, source_slug}, total_games: {...}, ...}`.
  It is `{}` when no market matched (pre-coverage, an un-listed regular-season game, or a
  game that can't be pinned to one date).

- **"Prediction to grade:"** — one TSV row: `prediction_id`, `video_id`, `category`,
  `prediction_text`.

Team names in the odds JSON are 3-letter abbreviations (e.g. `BOS`, `PHI`, `GSW`). To map a
team name in the prediction text to its abbreviation, use the canonical codes in
`docs/team-abbreviations.md` verbatim — never guess an abbreviation.

## What to do for this prediction

1. **Find the source file:** the prediction lives in the `predictions.json` whose episode
   folder name contains its `video_id` (under `podcasts/`). Locate that file and the object
   whose `prediction_id` matches.

2. **Read which team the prediction backs** from `prediction_text` — the team it asserts
   will win the game / win the series. (For a negated or compound claim, this is still the
   team the claim is *about winning*, e.g. "OKC sweep PHX 4-0" backs OKC.)

3. **Copy the market benchmark into three fields — no judgement on the number.** These come
   **straight from the provided "Market odds" JSON**; you only route an already-computed
   number to the team the prediction backs. Set on the matched object:
   - `"market_prob"` — the implied start probability the market gave the **outcome the
     prediction backed**: for a `game`, `probs[<team predicted to win>]`; for a `series`,
     `winner.probs[<team predicted to win the series>]`. Copy the number verbatim.
   - `"market_favorite"` — the abbrev the market favored at start: `favored` for a `game`,
     `winner.favored` for a `series`.
   - `"market_source"` — the `source_slug`(s) used (for a `series`, the winner's
     `source_slug`; include the total-games slug too if present).
   **If the "Market odds" JSON is `{}`, set all three fields to `""`.** Do not invent or
   estimate a probability.

**Change nothing else in the file.** Never touch `status`, `grade_note`, `status_general`,
`grade_note_general`, or any other field, and leave every other prediction untouched.

Do not touch `predictions.db`, do not run sync, do not commit — the shell does that.
Fill the three market fields for this prediction, then stop.
