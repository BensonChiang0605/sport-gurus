#!/bin/bash
set -eo pipefail

# Batch-summarise podcast episodes for a specific podcast folder.
# Each iteration summarises ONE episode that still lacks a summary.md, then commits.
# Stops when no unsummarised episodes remain, or after <iterations> runs.
#
# Usage: ./ralph/afk.sh <iterations> <podcast-path> [start-episode] [end-episode]
# e.g.   ./ralph/afk.sh 20 podcasts/basketball/draymond-green-show
#        ./ralph/afk.sh 20 podcasts/basketball/draymond-green-show 2026-05-10-episode
#        ./ralph/afk.sh 20 podcasts/basketball/draymond-green-show 2026-05-10-episode 2026-05-20-episode

if [ -z "$1" ] || [ -z "$2" ]; then
    echo "Usage: $0 <iterations> <podcast-path> [start-episode]"
    exit 1
fi

podcast_path="$2"
start_episode="${3:-}"
end_episode="${4:-}"
stream_text='select(.type == "assistant").message.content[]? | select(.type == "text").text // empty'

for ((i=1; i<=$1; i++)); do
    # Find oldest episode with transcript.txt but no summary.md, at or after start_episode
    next_episode=$(find "$podcast_path/episodes" -mindepth 1 -maxdepth 1 -type d \
        | while IFS= read -r dir; do
            [ -f "$dir/transcript.txt" ] && [ ! -f "$dir/summary.md" ] && basename "$dir"
          done \
        | sort \
        | awk -v start="$start_episode" -v end="$end_episode" '(!start || $0 >= start) && (!end || $0 <= end)' \
        | head -1)

    if [ -z "$next_episode" ]; then
        echo "No more tasks after $((i-1)) iterations."
        exit 0
    fi

    episode_path="$podcast_path/episodes/$next_episode"
    commits=$(git log -n 5 --format="%H%n%ad%n%B---" --date=short 2>/dev/null || echo "No commits found")
    prompt=$(cat ralph/prompt.md)

    claude \
        --model claude-sonnet-4-6 \
        --permission-mode bypassPermissions \
        --verbose \
        --print \
        --output-format stream-json \
        "Previous commits: $commits Episode to process: $episode_path $prompt" \
    | grep --line-buffered '^{' \
    | jq --unbuffered -rj "$stream_text"

    python3 ralph/sync.py
    git add -A
    git commit -m "summarise $next_episode"
done
