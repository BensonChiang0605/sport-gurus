#!/bin/bash

usage_llm() {
    echo "Invalid --llm value: $RALPH_LLM" >&2
    echo "Expected one of: claude, gpt" >&2
}

validate_llm() {
    RALPH_LLM="${RALPH_LLM:-claude}"

    case "$RALPH_LLM" in
        claude|gpt)
            ;;
        *)
            usage_llm
            return 1
            ;;
    esac
}

parse_llm_flag() {
    RALPH_LLM="${RALPH_LLM:-claude}"
    RALPH_LLM_ARGC=0

    if [ "${1:-}" = "--llm" ]; then
        if [ -z "${2:-}" ]; then
            echo "Missing value for --llm" >&2
            return 1
        fi

        RALPH_LLM="$2"
        RALPH_LLM_ARGC=2
    fi

    validate_llm || return 1
}

run_llm() {
    prompt="$1"

    validate_llm || return 1

    case "$RALPH_LLM" in
        claude)
            stream_text='select(.type == "assistant").message.content[]? | select(.type == "text").text // empty'

            claude \
                --model "${RALPH_CLAUDE_MODEL:-claude-sonnet-4-6}" \
                --permission-mode bypassPermissions \
                --verbose \
                --print \
                --output-format stream-json \
                "$prompt" \
            | grep --line-buffered '^{' \
            | jq --unbuffered -rj "$stream_text"
            ;;
        gpt)
            codex exec \
                --model "${RALPH_GPT_MODEL:-gpt-5}" \
                --cd . \
                --sandbox workspace-write \
                --ask-for-approval never \
                "$prompt"
            ;;
    esac
}
