# TASK: Summarise ONE podcast episode

You analyse the prediction accuracy of sports podcasts. Each run you process exactly
**one** episode, then stop. The loop driver re-invokes you for the next one.

## 1. Pick the next episode

Scan `podcasts/*/*/episodes/*/` for an episode folder that has a `transcript.txt` but
**no** `summary.md`. Pick the **oldest** such folder (folder names start with the upload
date `YYYY-MM-DD`, so the lowest name is oldest).

If every episode already has a `summary.md`, output exactly this and do nothing else:

<promise>NO MORE TASKS</promise>

## 2. Read it

Read that folder's `transcript.txt` (the full episode text) and `meta.json` (video_id,
title, url, episode_date, podcast, sport).

## 3. Write `summary.md` in that folder

Use this structure:

```markdown
---
podcast: <podcast slug from meta.json>
video_id: <video_id>
episode_date: <episode_date>
title: <title>
url: <url>
---

## Summary

<3–6 sentences on what the episode covered.>

## Verifiable predictions

- <each objectively-checkable prediction the host/guests made, one bullet>
```

## 4. Append predictions to the sport's ledger

The ledger is `podcasts/<sport>/predictions.csv` (e.g. `podcasts/basketball/predictions.csv`).
Append one row per **objectively verifiable** prediction. Columns:

`prediction_id,podcast,video_id,episode_date,speaker,prediction_text,category,verifiable,resolve_after,status,evidence`

- `prediction_id`: `<video_id>-NN` (NN = 01, 02, … within the episode).
- `speaker`: who made the claim (e.g. `Draymond Green`, or a guest's name).
- `prediction_text`: a concise paraphrase of the claim.
- `category`: one of `game | series | award | season`.
- `verifiable`: `auto` if checkable from NBA game records (a game/series result),
  `manual` if objective but needs another source (awards, signings, stat lines).
- `resolve_after`: the date the outcome becomes known (`YYYY-MM-DD`), best estimate.
- `status`: always `pending` (grading is a separate job).
- `evidence`: leave blank.

**Only log claims that can be objectively settled true/false.** Discard subjective takes,
opinions, and vibes ("they have championship DNA", "he's the most clutch"). If an episode
contains no verifiable predictions, write the `summary.md` with an empty predictions list
and append no rows.

Quote rules: CSV fields containing commas must be wrapped in double quotes.

## 5. Commit

`git add -A && git commit` with message `summarise <episode-folder-name>`. Then stop.
