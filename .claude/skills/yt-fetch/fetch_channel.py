import argparse
from datetime import datetime
from zoneinfo import ZoneInfo
import yt_dlp

TAIPEI = ZoneInfo("Asia/Taipei")


def fetch_channel(channel_handle: str, max_results: int = 5) -> list[dict]:
    url = f"https://www.youtube.com/@{channel_handle}/videos"
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "playlistend": max_results,
        "ignoreerrors": True,
    }

    results = []
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        for entry in info.get("entries", []):
            if entry is None:
                continue
            timestamp = entry.get("timestamp")
            upload_date_str = entry.get("upload_date")
            results.append({
                "title": entry.get("title"),
                "upload_date": (
                    datetime.fromtimestamp(timestamp, tz=TAIPEI).strftime("%Y-%m-%d")
                    if timestamp
                    else datetime.strptime(upload_date_str, "%Y%m%d").strftime("%Y-%m-%d")
                    if upload_date_str
                    else None
                ),
                "upload_datetime": (
                    datetime.fromtimestamp(timestamp, tz=TAIPEI).isoformat()
                    if timestamp
                    else None
                ),
                "url": entry.get("webpage_url") or f"https://youtube.com/watch?v={entry.get('id')}",
                "duration": entry.get("duration"),
                "video_id": entry.get("id"),
            })
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", required=True, help="YouTube channel handle")
    parser.add_argument("--max", type=int, default=5, help="Number of videos to fetch")
    args = parser.parse_args()

    videos = fetch_channel(args.channel, args.max)
    for v in videos:
        print(f"{v['upload_date']}  {v['title']}")
        print(f"  {v['url']}")
