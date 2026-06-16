#!/bin/bash
set -eo pipefail

# Batch-summarise podcast episodes. Each iteration summarises ONE episode that
# still lacks a summary.md, then commits. Stops when the agent reports no work
# left, or after <iterations> runs.
#
# Usage: ./ralph/afk.sh <iterations>

if [ -z "$1" ]; then
    echo "Usage: $0 <iterations>"
    exit 1
fi

# jq filter to extract streaming text from assistant messages
stream_text='select(.type == "assistant").message.content[]? | select(.type == "text").text // empty'

# jq filter to extract final result
final_result='select(.type == "result").result // empty'

for ((i=1; i<=$1; i++)); do
    tmpfile=$(mktemp)
    trap "rm -f $tmpfile" EXIT

    commits=$(git log -n 5 --format="%H%n%ad%n%B---" --date=short 2>/dev/null || echo "No commits found")
    prompt=$(cat ralph/prompt.md)

    claude \
        --permission-mode bypassPermissions \
        --verbose \
        --print \
        --output-format stream-json \
        "Previous commits: $commits $prompt" \
    | grep --line-buffered '^{' \
    | tee "$tmpfile" \
    | jq --unbuffered -rj "$stream_text"

    result=$(jq -r "$final_result" "$tmpfile")

    if [[ "$result" == *"<promise>NO MORE TASKS</promise>"* ]]; then
        echo "Ralph complete after $i iterations."
        exit 0
    fi
done
