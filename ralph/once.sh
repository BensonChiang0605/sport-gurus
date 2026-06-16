#!/bin/bash

# Summarise a single episode in an interactive Claude session (one iteration of
# the ralph loop). Use ralph/afk.sh to batch many.

commits=$(git log -n 5 --format="%H%n%ad%n%B---" --date=short 2>/dev/null || echo "No commits found")
prompt=$(cat ralph/prompt.md)

claude --permission-mode bypassPermissions \
  "Previous commits: $commits $prompt"
