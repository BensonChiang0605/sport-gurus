#!/usr/bin/env python3
"""
Sync all predictions.json files into predictions.db (SQLite).
Also migrates existing predictions.csv files for episodes that predate the
predictions.json workflow. Idempotent — safe to run multiple times.
"""
import csv, json, pathlib, sqlite3

DB = pathlib.Path("predictions.db")
COLS = [
    "prediction_id", "podcast", "video_id", "episode_date", "episode_datetime", "speaker",
    "prediction_text", "category", "verifiable", "status", "argument",
    "grade_note", "status_general", "grade_note_general",
    "market_prob", "market_favorite", "market_source",
]

db = sqlite3.connect(DB)
# Drop & recreate so the schema always matches COLS (e.g. when columns are added).
# Safe because every row is re-inserted from the CSV + JSON source files below.
db.execute("DROP TABLE IF EXISTS predictions")
db.execute(f"""
    CREATE TABLE predictions (
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

# Legacy: predictions.csv files for episodes that predate predictions.json.
# Loaded FIRST so that predictions.json (the source of truth) overrides any stale
# duplicate rows via INSERT OR REPLACE — e.g. once an episode is migrated to JSON
# and graded, its JSON grades must win over the original pending CSV rows.
for f in sorted(pathlib.Path("podcasts").rglob("predictions.csv")):
    with open(f, newline='') as fh:
        upsert(list(csv.DictReader(fh)))

# Primary source: predictions.json files written by the LLM (override legacy CSV)
for f in sorted(pathlib.Path("podcasts").rglob("predictions.json")):
    upsert(json.loads(f.read_text()))

db.commit()
count = db.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]
print(f"{count} predictions in {DB}")
