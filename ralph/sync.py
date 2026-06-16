#!/usr/bin/env python3
"""
Sync all predictions.json files into predictions.db (SQLite).
Also migrates existing predictions.csv files for episodes that predate the
predictions.json workflow. Idempotent — safe to run multiple times.
"""
import csv, json, pathlib, sqlite3

DB = pathlib.Path("predictions.db")
COLS = [
    "prediction_id", "podcast", "video_id", "episode_date", "speaker",
    "prediction_text", "category", "verifiable", "status", "argument",
]

db = sqlite3.connect(DB)
db.execute(f"""
    CREATE TABLE IF NOT EXISTS predictions (
        {', '.join(c + ' TEXT' for c in COLS)},
        PRIMARY KEY (prediction_id)
    )
""")

def upsert(rows):
    if not rows:
        return
    placeholders = ','.join('?' * len(COLS))
    db.executemany(
        f"INSERT OR REPLACE INTO predictions ({','.join(COLS)}) VALUES ({placeholders})",
        [[r.get(c, '') for c in COLS] for r in rows],
    )

# Primary source: predictions.json files written by the LLM
for f in sorted(pathlib.Path("podcasts").rglob("predictions.json")):
    upsert(json.loads(f.read_text()))

# Migration: predictions.csv files for episodes that predate predictions.json
for f in sorted(pathlib.Path("podcasts").rglob("predictions.csv")):
    with open(f, newline='') as fh:
        upsert(list(csv.DictReader(fh)))

db.commit()
count = db.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]
print(f"{count} predictions in {DB}")
