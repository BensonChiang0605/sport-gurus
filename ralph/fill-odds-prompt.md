# TASK: Attach the market benchmark (Polymarket odds) to one NBA prediction

You route an already-computed prediction-market probability onto a single `game` or
`series` prediction's source file. This is **not** grading — you do not judge whether the
prediction came true, and you must not touch any grade. The only judgement here is reading
which team the prediction backs, so you can copy the number for that side.

## Inputs (passed in your instructions)

- **"Market odds (Polymarket, as of the episode datetime; pregame fallback):"** — JSON with
  the prediction-market-implied probability *as of when the episode aired* (the moment the
  prediction was made), falling back to *the start of the game/series* when no market price
  existed yet at episode time. Already fetched for you (deterministic). Shapes:
  - `game` → `{probs: {ABBR: p, ABBR: p}, favored, source_slug, ...}`.
  - `series` → `{winner: {probs: {ABBR: p, ...}, favored, source_slug},
    total_games: {line, over_prob, under_prob, source_slug}, ...}`. The `total_games`
    block is **optional** — it may be absent even when `winner` is present.
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

2. **Read which side the prediction backs** from `prediction_text`. The benchmark you route
   must be the market's probability of **the literal claim coming true** — so identify the
   team whose winning makes the claim true:
   - Plain win claim ("BOS beat PHI", "OKC win the series") → that team.
   - **Negated** win claim ("MIN will *not* close out DEN in Game 6", "PHX do not win another
     game") → the claim is true when the **opponent** wins, so back the **opponent** (DEN, the
     team that wins the game if the claim holds). Route the opponent's win probability.
   - Compound claim with a winner ("OKC sweep PHX 4-0") still backs the winning team (OKC).

   **Point-margin / margin-of-victory / vague-magnitude claims have no market — skip them.**
   If the claim is about *how much* a team wins by rather than *whether* it wins — e.g. "win by
   40 points", "by double digits", "blow out", "dominate", a specific final score — there is no
   Polymarket benchmark for a margin. **Set all six fields to `""` and stop**, regardless of the
   provided odds (the game moneyline is *not* a valid stand-in for a margin claim).

3. **Route the market benchmark into the fields — no judgement on any number.** Every number
   comes **straight from the provided "Market odds" JSON**; you never invent or estimate one.
   There are two parallel triples, mirroring the two series grades (`status` exact vs
   `status_general`): an **exact** triple (`market_prob` / `market_favorite` / `market_source`)
   and a **general** triple (`market_prob_general` / `market_favorite_general` /
   `market_source_general`). You do **no arithmetic** — `under_prob` is precomputed for you.

   **If the "Market odds" JSON is `{}`, set all six fields to `""`** and stop.

   **General triple — series-winner odds (who wins the series, ignoring game count):**
   - For a `game`: leave all three `*_general` fields `""`.
   - For a `series`: `market_prob_general = winner.probs[<team predicted to win the series>]`,
     `market_favorite_general = winner.favored`, `market_source_general = winner.source_slug`.

   **Exact triple — the full claim as stated:**
   - For a `game`: `market_prob = probs[<team predicted to win>]`,
     `market_favorite = favored`, `market_source = source_slug` (unchanged behaviour).
   - For a `series` **with a game count** (e.g. "in 5 games", "in six", "goes to 7"), use the
     `total_games` block. Read the count `n` from `prediction_text` and compare it to
     `total_games.line` (always an `X.5`):
     - `n < line` → `market_prob = total_games.under_prob`, `market_favorite = "under"`.
     - `n > line` → `market_prob = total_games.over_prob`,  `market_favorite = "over"`.
     - Set `market_source = total_games.source_slug`.
     - **If there is no `total_games` block**, leave the exact triple `""` (the general triple
       is still filled). The count claim has no market benchmark here.
   - For a `series` with **no game count** (e.g. "BOS win vs PHI"), the exact claim *is* the
     winner claim: mirror the exact triple from the general triple — copy
     `market_prob = market_prob_general`, `market_favorite = market_favorite_general`,
     `market_source = market_source_general`.

**Change nothing else in the file.** Never touch `status`, `grade_note`, `status_general`,
`grade_note_general`, or any other field, and leave every other prediction untouched.

Do not touch `predictions.db`, do not run sync, do not commit — the shell does that.
Fill the market fields for this prediction, then stop.
