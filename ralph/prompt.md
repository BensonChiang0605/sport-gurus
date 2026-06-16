# TASK: Summarise ONE podcast episode

You analyse the prediction accuracy of sports podcasts. The episode folder to process
has been identified for you and passed as **"Episode to process: <path>"** in your
instructions. Process exactly that episode, then stop.

## 1. Read it

Read that folder's `transcript.txt` (the full episode text) and `meta.json` (video_id,
title, url, episode_date, podcast, sport).

## 2. Write `summary.md` in that folder

Use this structure. For each prediction, add sub-bullets capturing the speaker's own
reasoning — injury context, matchup reads, momentum, stats, whatever they cite. Bold
the sub-bullet label. Include only rationale the speaker actually gave; don't invent it.

```markdown
---
podcast: <podcast slug from meta.json>
video_id: <video_id>
episode_date: <episode_date>
title: <title>
url: <url>
---

## Verifiable predictions

- <prediction stated as a full sentence, naming the speaker and the specific claim>
  * **<Reason label>:** <the speaker's stated rationale or supporting evidence>
  * **<Reason label>:** <additional rationale if given>
```

## 3. Write `predictions.json` in that folder

Write a JSON array to `<episode_path>/predictions.json`. One object per objectively
verifiable prediction, using exactly these keys:

```json
[
  {
    "prediction_id": "<video_id>-NN",
    "podcast": "<podcast slug from meta.json>",
    "video_id": "<video_id>",
    "episode_date": "<episode_date>",
    "speaker": "<who made the claim>",
    "prediction_text": "<concise paraphrase of the claim>",
    "category": "<game | series | award | season>",
    "verifiable": "<auto | manual>",
    "status": "pending",
    "argument": "<Label: one-sentence rationale; Label2: one-sentence rationale>"
  }
]
```

- `prediction_id`: `<video_id>-NN` (NN = 01, 02, … within the episode).
- `verifiable`: `auto` if checkable from NBA game records; `manual` if objective but
  needs another source (awards, signings, stat lines).
- `status`: always `"pending"`.
- `argument`: Populate from the reason sub-bullets written for this prediction in
  `summary.md`. Format as `"Label: short rationale; Label2: short rationale"` — use
  the same labels, one sentence per clause. Use `""` only if the speaker gave no
  rationale.

**Only log claims that can be objectively settled true/false.** Discard subjective takes,
opinions, and vibes. If there are no verifiable predictions, write `[]`.
