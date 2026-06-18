#!/bin/bash
set -eo pipefail

# Attach Polymarket pregame odds to graded/ungraded game/series predictions (one pass).
# Deterministic work lives here; only the routing judgement (which side the claim backs)
# lives in the LLM. This is decoupled from grading on purpose: odds get a fresh market
# listing on a different cadence than results, so they can be (re)filled without re-grading.
#
#   1. pull game/series predictions from predictions.db (default: those missing odds)
#   2. for each: fetch its pregame odds + call Claude/GPT to route them into predictions.json
#   3. re-sync the DB and commit
#
# Usage: ./ralph/fill-odds.sh [--llm claude|gpt] [--all]
#   --all   re-route every game/series prediction (e.g. after a cache rebuild),
#           not just the ones currently missing odds.

usage() {
    echo "Usage: $0 [--llm claude|gpt] [--all]"
}

source "$(dirname "$0")/llm.sh"

if ! parse_llm_flag "$@"; then
    usage
    exit 1
fi

if [ "${RALPH_LLM_ARGC:-0}" -gt 0 ]; then
    shift "$RALPH_LLM_ARGC"
fi

all=false
if [ "${1:-}" = "--all" ]; then
    all=true
    shift
fi

if [ -n "${1:-}" ]; then
    usage
    exit 1
fi

# Predictions to fill — prediction_text last so stray tabs don't shift fields.
# Default to rows still missing a market probability; --all re-routes everything.
where="category IN ('game','series')"
if [ "$all" = false ]; then
    where="$where AND (market_prob='' OR market_prob IS NULL)"
fi
predictions=$(sqlite3 -separator $'\t' predictions.db \
    "SELECT prediction_id, video_id, category, prediction_text FROM predictions WHERE $where")

if [ -z "$predictions" ]; then
    echo "No game/series predictions to fill."
    exit 0
fi

prompt=$(cat ralph/fill-odds-prompt.md)

# Route one prediction at a time, each with odds scoped to its matchup.
while IFS=$'\t' read -r pid vid category ptext; do
    [ -z "$pid" ] && continue
    echo "Filling odds for $pid ..."

    # Deterministic market benchmark — lazy Polymarket odds cache (fills on first use,
    # fail-soft to {} so the pass never blocks). The LLM only routes a number, see prompt.
    odds=$(uv run scripts/polymarket_odds.py --odds-for-text "$ptext" --category "$category" || true)

    # No market matched -> nothing to route. The fields stay empty; skip the LLM call.
    if [ -z "$odds" ] || [ "$odds" = "{}" ]; then
        echo "  no market odds; leaving fields empty"
        continue
    fi

    run_llm "Market odds (Polymarket, pregame): $odds

Prediction to grade (TSV: prediction_id, video_id, category, prediction_text):
$pid	$vid	$category	$ptext

$prompt" \
    || true
    echo
done <<< "$predictions"

# Re-sync the DB from the edited predictions.json files and commit.
python3 ralph/sync.py
git add -A
git diff --staged --quiet || git commit -m "fill market odds onto game/series predictions"
