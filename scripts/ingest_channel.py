"""Ingest a podcast's YouTube episodes into the cleaned `episodes/` layout.

Joins two data sources on `video_id`:
  - transcripts  : ytfetcher  (`uvx ytfetcher channel <handle> -m <n> -f json`)
  - upload dates : yt-fetch    (.claude/skills/yt-fetch/fetch_channel.py)

For each episode it creates
    podcasts/<sport>/<podcast>/episodes/<YYYY-MM-DD>_<video_id>/transcript.txt
containing the combined transcript text with per-line timestamps stripped.

No raw JSON is persisted; the cleaned episode folders are the source of truth.
Re-runs are idempotent: episodes whose folder already exists are skipped.

Examples
--------
Live fetch a new podcast:
    uv run scripts/ingest_channel.py --channel DraymondGreenShow \
        --sport basketball --podcast draymond-green-show --max 5

Migrate from an existing ytfetcher dump (no re-download of transcripts):
    uv run scripts/ingest_channel.py --channel DraymondGreenShow \
        --sport basketball --podcast draymond-green-show --from-json data.json
"""

import argparse
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
YT_FETCH = REPO_ROOT / ".claude" / "skills" / "yt-fetch" / "fetch_channel.py"


def _load_fetch_channel():
    """Import `fetch_channel` from the yt-fetch skill by file path."""
    spec = importlib.util.spec_from_file_location("yt_fetch", YT_FETCH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.fetch_channel


def get_transcripts(channel: str, max_results: int, from_json: str | None) -> list[dict]:
    """Return ytfetcher episode dicts, from an existing dump or a live fetch."""
    if from_json:
        return json.loads(Path(from_json).read_text())

    # Live fetch: stream the JSON straight from ytfetcher's stdout.
    proc = subprocess.run(
        ["uvx", "ytfetcher", "channel", channel,
         "-m", str(max_results), "-f", "json", "--stdout"],
        check=True, capture_output=True, text=True,
    )
    return json.loads(proc.stdout)


def clean_transcript(segments: list[dict]) -> str:
    """Join transcript segments into continuous prose, dropping timestamps."""
    text = " ".join(seg.get("text", "").strip() for seg in segments)
    return re.sub(r"\s+", " ", text).strip()


def fetch_metadata(channel: str, want_ids: set[str], window: int,
                   cache_path: Path) -> dict[str, dict]:
    """Resolve upload dates for `want_ids`, caching results to disk.

    yt-dlp throttles bulk full-extraction, so this makes at most ONE network
    call per run (only when ids are still missing) and persists every date it
    gets. A partial/throttled run therefore self-heals on the next invocation
    without re-fetching ids already cached.
    """
    cache: dict[str, dict] = {}
    if cache_path.exists():
        cache = json.loads(cache_path.read_text())

    if not want_ids <= cache.keys():
        fetch_channel = _load_fetch_channel()
        try:
            for m in fetch_channel(channel, window):
                if m.get("upload_date"):
                    cache[m["video_id"]] = m
        except Exception as exc:  # noqa: BLE001 - yt-dlp raises a variety of errors
            print(f"  ! metadata fetch failed ({exc})")
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache, indent=2) + "\n")

    still_missing = want_ids - cache.keys()
    if still_missing:
        print(f"  ! {len(still_missing)} ids still missing dates "
              f"(likely throttled); re-run to fill from cache")
    return cache


def ingest(channel: str, sport: str, podcast: str, max_results: int,
           from_json: str | None) -> None:
    episodes = get_transcripts(channel, max_results, from_json)

    base = REPO_ROOT / "podcasts" / sport / podcast / "episodes"
    # Fetch upload dates for a window wide enough to cover every transcript episode,
    # caching them so re-runs don't re-hammer YouTube.
    want_ids = {ep["video_id"] for ep in episodes}
    meta = fetch_metadata(
        channel, want_ids,
        window=max(max_results, len(episodes) + 10),
        cache_path=base.parent / ".video_meta.json",
    )
    created = skipped = missing = 0

    for ep in episodes:
        vid = ep["video_id"]
        info = meta.get(vid)
        if not info or not info.get("upload_date"):
            print(f"  ! no upload date for {vid} ({ep.get('title')!r}); skipping")
            missing += 1
            continue

        folder = base / f"{info['upload_date']}_{vid}"
        transcript_path = folder / "transcript.txt"
        if transcript_path.exists():
            skipped += 1
            continue

        folder.mkdir(parents=True, exist_ok=True)
        transcript_path.write_text(clean_transcript(ep["transcript"]) + "\n")
        episode_meta = {
            "video_id": vid,
            "title": ep.get("title") or info.get("title"),
            "url": ep.get("url") or info.get("url") or f"https://www.youtube.com/watch?v={vid}",
            "episode_date": info["upload_date"],
            "duration": ep.get("duration") or info.get("duration"),
            "podcast": podcast,
            "sport": sport,
        }
        (folder / "meta.json").write_text(json.dumps(episode_meta, indent=2) + "\n")
        created += 1
        print(f"  + {folder.relative_to(REPO_ROOT)}")

    print(f"\nDone: {created} created, {skipped} skipped, {missing} missing dates.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--channel", required=True, help="YouTube channel handle")
    parser.add_argument("--sport", required=True, help="e.g. basketball")
    parser.add_argument("--podcast", required=True, help="slug, e.g. draymond-green-show")
    parser.add_argument("--max", type=int, default=5, dest="max_results",
                        help="episodes to fetch live (ignored with --from-json)")
    parser.add_argument("--from-json", default=None,
                        help="path to an existing ytfetcher JSON dump")
    args = parser.parse_args()

    ingest(args.channel, args.sport, args.podcast, args.max_results, args.from_json)


if __name__ == "__main__":
    sys.exit(main())
