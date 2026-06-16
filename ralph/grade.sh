#!/bin/bash
set -eo pipefail

# Grade pending `game`/`series` predictions against NBA results (one grading pass).
# Deterministic work lives here; only the true/false judgement lives in the LLM.
#
#   1. refresh the game cache from nba_api
#   2. build the deterministic fact bundle (series + play-in + raw games)
#   3. pull the pending auto game/series predictions from predictions.db
#   4. hand facts + predictions to Claude, which edits each predictions.json status
#   5. re-sync the DB and commit
#
# Usage: ./ralph/grade.sh

# 1. Refresh the committed games cache.
uv run scripts/nba_games.py --write

# 2. Deterministic facts the LLM judges against.
facts=$(uv run scripts/nba_games.py --facts)

# 3. Pending predictions to grade (easy/fast query surface).
predictions=$(sqlite3 -separator $'\t' predictions.db \
    "SELECT prediction_id, video_id, prediction_text, category
     FROM predictions
     WHERE status='pending' AND verifiable='auto' AND category IN ('game','series')")

if [ -z "$predictions" ]; then
    echo "No pending game/series predictions to grade."
    exit 0
fi

prompt=$(cat ralph/grade-prompt.md)
stream_text='select(.type == "assistant").message.content[]? | select(.type == "text").text // empty'

claude \
    --model claude-sonnet-4-6 \
    --permission-mode bypassPermissions \
    --verbose \
    --print \
    --output-format stream-json \
    "Facts: $facts

Predictions to grade (TSV: prediction_id, video_id, prediction_text, category):
$predictions

$prompt" \
| grep --line-buffered '^{' \
| jq --unbuffered -rj "$stream_text"

# 5. Re-sync the DB from the edited predictions.json files and commit.
python3 ralph/sync.py
git add -A
git commit -m "grade pending game/series predictions"
