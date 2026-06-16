---
name: yt-fetch
description: Fetch YouTube channel videos and their upload dates using yt-dlp. Use when the user wants to pull recent videos, upload dates, or metadata from a YouTube channel handle.
---

Use the script at `.claude/skills/yt-fetch/fetch_channel.py` to fetch videos and upload dates from a YouTube channel.

Run it with:
```
uv run .claude/skills/yt-fetch/fetch_channel.py --channel <handle> --max <n>
```

- `--channel` is the YouTube channel handle (e.g. `DraymondGreenShow`)
- `--max` is the number of recent videos to fetch (default: 5)

Print results to stdout and return them to the user in a table.
