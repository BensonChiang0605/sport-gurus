#!/bin/bash

# Summarise a single episode in an interactive Claude session (one iteration of
# the ralph loop). Use ralph/afk.sh to batch many.
#
# Usage: ./ralph/once.sh <podcast-path> [episode-name]
# e.g.   ./ralph/once.sh podcasts/basketball/draymond-green-show
#        ./ralph/once.sh podcasts/basketball/draymond-green-show 2026-05-10-some-episode

if [ -z "$1" ]; then
    echo "Usage: $0 <podcast-path> [episode-name]"
    exit 1
fi

podcast_path="$1"

if [ -n "$2" ]; then
    next_episode="$2"
else
    # Find oldest episode with transcript.txt but no summary.md
    next_episode=$(find "$podcast_path/episodes" -mindepth 1 -maxdepth 1 -type d \
        | while IFS= read -r dir; do
            [ -f "$dir/transcript.txt" ] && [ ! -f "$dir/summary.md" ] && basename "$dir"
          done \
        | sort | head -1)

    if [ -z "$next_episode" ]; then
        echo "No more tasks."
        exit 0
    fi
fi

episode_path="$podcast_path/episodes/$next_episode"
commits=$(git log -n 5 --format="%H%n%ad%n%B---" --date=short 2>/dev/null || echo "No commits found")
prompt=$(cat ralph/prompt.md)

stream_text='select(.type == "assistant").message.content[]? | select(.type == "text").text // empty'

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
