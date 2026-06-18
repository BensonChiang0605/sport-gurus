#!/bin/bash
set -eo pipefail

# Grade pending `game`/`series` predictions against NBA results (one grading pass).
# Deterministic work lives here; only the true/false judgement lives in the LLM.
#
#   1. refresh the game cache from nba_api
#   2. pull the pending auto game/series predictions from predictions.db
#   3. for each prediction: build scoped facts + call Claude/GPT to edit its predictions.json
#   4. re-sync the DB and commit
#
# Usage: ./ralph/grade.sh [--llm claude|gpt]
# e.g.   ./ralph/grade.sh --llm gpt

usage() {
    echo "Usage: $0 [--llm claude|gpt]"
}

source "$(dirname "$0")/llm.sh"

if ! parse_llm_flag "$@"; then
    usage
    exit 1
fi

if [ "${RALPH_LLM_ARGC:-0}" -gt 0 ]; then
    shift "$RALPH_LLM_ARGC"
fi

if [ -n "${1:-}" ]; then
    usage
    exit 1
fi

# 1. Refresh the committed games cache once.
uv run scripts/nba_games.py --write

# 2. Pending predictions to grade — prediction_text last so stray tabs don't shift fields.
predictions=$(sqlite3 -separator $'\t' predictions.db \
    "SELECT prediction_id, video_id, category, prediction_text
     FROM predictions
     WHERE status='pending' AND verifiable='auto' AND category IN ('game','series')")

if [ -z "$predictions" ]; then
    echo "No pending game/series predictions to grade."
    exit 0
fi

prompt=$(cat ralph/grade-prompt.md)

# 3. Grade one prediction at a time, each with facts scoped to its teams.
while IFS=$'\t' read -r pid vid category ptext; do
    [ -z "$pid" ] && continue
    echo "Grading $pid ..."

    # Deterministic scoped facts — reads from cache (no network), offline and fast.
    facts=$(uv run scripts/nba_games.py --facts-for-text "$ptext")

    # Deterministic market benchmark — lazy Polymarket odds cache (fills on first use,
    # fail-soft to {} so grading never blocks). The LLM only routes a number, see prompt.
    odds=$(uv run scripts/polymarket_odds.py --odds-for-text "$ptext" --category "$category" || true)

    run_llm "Facts (scoped to this prediction's teams): $facts

Market odds (Polymarket, pregame): $odds

Prediction to grade (TSV: prediction_id, video_id, category, prediction_text):
$pid	$vid	$category	$ptext

$prompt" \
    || true
    echo
done <<< "$predictions"

# 4. Re-sync the DB from the edited predictions.json files and commit.
python3 ralph/sync.py
git add -A
git commit -m "grade pending game/series predictions"
