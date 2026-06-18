#!/usr/bin/env python3
"""
Evaluate a podcast speaker's predictions for sports-betting value: how the speaker's
picks compare to the market's own pregame pricing (Polymarket implied probability),
not just raw right/wrong accuracy. Renders a static HTML report.

Usage: uv run scripts/draymond_value.py [--speaker "Draymond Green"]
"""
import argparse
import json
import pathlib
import sqlite3

ROOT = pathlib.Path(__file__).resolve().parent.parent
DB = ROOT / "predictions.db"
REPORT = ROOT / "reports" / "draymond_value.html"

GRADED = {"correct", "incorrect"}


def fetch_rows(speaker):
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    return db.execute(
        "SELECT * FROM predictions WHERE speaker = ? AND category IN ('game', 'series')",
        (speaker,),
    ).fetchall()


def _pick(row, status_col, prob_col, side):
    status = row[status_col]
    prob = row[prob_col]
    if status not in GRADED or prob in ("", None):
        return None
    prob = float(prob)
    outcome = 1 if status == "correct" else 0
    return {
        "prediction_id": row["prediction_id"],
        "side": side,
        "prob": prob,
        "outcome": outcome,
        "edge": outcome - prob,
        "profit": (1 / prob - 1) if outcome else -1.0,
        "bucket": "favorite" if prob > 0.5 else "underdog",
        "episode_date": row["episode_date"],
        "prediction_text": row["prediction_text"],
    }


def build_picks(rows):
    """One pick per game; for series, one pick per distinct market (total-games O/U
    and series-winner) unless the prediction had no game count, in which case both
    markets are routed to the same Polymarket source and must be counted once, not
    twice."""
    picks = []
    for row in rows:
        if row["category"] == "game":
            p = _pick(row, "status", "market_prob", "exact")
            if p:
                picks.append(p)
            continue

        mirrored = row["market_source"] != "" and row["market_source"] == row["market_source_general"]
        if mirrored:
            p = _pick(row, "status", "market_prob", "exact")
            if p:
                picks.append(p)
        else:
            for status_col, prob_col, side in (
                ("status", "market_prob", "exact"),
                ("status_general", "market_prob_general", "general"),
            ):
                p = _pick(row, status_col, prob_col, side)
                if p:
                    picks.append(p)
    return picks


def metrics_for(picks):
    n = len(picks)
    if n == 0:
        return {"n": 0, "agreement_rate": None, "accuracy": None, "mean_edge": None, "roi": None}
    favorite_n = sum(1 for p in picks if p["bucket"] == "favorite")
    return {
        "n": n,
        "agreement_rate": favorite_n / n,
        "accuracy": sum(p["outcome"] for p in picks) / n,
        "mean_edge": sum(p["edge"] for p in picks) / n,
        "roi": sum(p["profit"] for p in picks) / n,
    }


def _escape(text):
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _pct(value):
    return "&mdash;" if value is None else f"{value:.1%}"


def _signed_pct(value):
    return "&mdash;" if value is None else f"{value:+.1%}"


def build_chart_data(picks, general_preferred):
    """Plain JSON-able data for the three Chart.js charts, all derived from `picks`."""
    ordered = sorted(picks, key=lambda p: (p["episode_date"], p["prediction_id"]))
    gp_keys = {(p["prediction_id"], p["side"]) for p in general_preferred}

    # 1. Cumulative profit — two running-sum lines aligned on the same x-axis. The
    # general-preferred line carries its value forward on picks not in that subset.
    run_all = run_gp = 0.0
    cum_all, cum_gp, pids = [], [], []
    for p in ordered:
        run_all += p["profit"]
        if (p["prediction_id"], p["side"]) in gp_keys:
            run_gp += p["profit"]
        cum_all.append(round(run_all, 3))
        cum_gp.append(round(run_gp, 3))
        pids.append(f"{p['prediction_id']} ({p['side']})")
    cumulative = {"labels": list(range(1, len(ordered) + 1)), "pids": pids,
                  "all": cum_all, "gp": cum_gp}

    # 2. Scatter — market prob (x) vs profit (y), split by outcome.
    def points(outcome):
        return [{"x": p["prob"], "y": round(p["profit"], 3),
                 "pid": f"{p['prediction_id']} ({p['side']})"}
                for p in picks if p["outcome"] == outcome]
    scatter = {"win": points(1), "loss": points(0)}

    # 3. Calibration — hit rate vs mean market prob across prob buckets.
    edges = [0.0, 0.25, 0.50, 0.75, 1.01]  # 1.01 so prob==1.0 lands in the last bucket
    labels, hit_rate, market_prob, counts = [], [], [], []
    for lo, hi in zip(edges, edges[1:]):
        bucket = [p for p in picks if lo <= p["prob"] < hi]
        labels.append(f"{int(lo * 100)}–{min(int(hi * 100), 100)}%")
        counts.append(len(bucket))
        if bucket:
            hit_rate.append(round(sum(p["outcome"] for p in bucket) / len(bucket), 3))
            market_prob.append(round(sum(p["prob"] for p in bucket) / len(bucket), 3))
        else:
            hit_rate.append(None)
            market_prob.append(None)
    calibration = {"labels": labels, "hit_rate": hit_rate,
                   "market_prob": market_prob, "counts": counts}

    return {"cumulative": cumulative, "scatter": scatter, "calibration": calibration}


def render_html(speaker, picks):
    all_m = metrics_for(picks)
    fav_m = metrics_for([p for p in picks if p["bucket"] == "favorite"])
    dog_m = metrics_for([p for p in picks if p["bucket"] == "underdog"])
    dates = sorted(p["episode_date"] for p in picks if p["episode_date"])
    date_range = f"{dates[0]} to {dates[-1]}" if dates else "n/a"

    rows = [
        ("Picks", all_m["n"], fav_m["n"], dog_m["n"]),
        ("Agreement rate (picked market favorite)", _pct(all_m["agreement_rate"]), "&mdash;", "&mdash;"),
        ("Raw accuracy", _pct(all_m["accuracy"]), _pct(fav_m["accuracy"]), _pct(dog_m["accuracy"])),
        ("Mean edge vs. market (outcome &minus; prob)", _signed_pct(all_m["mean_edge"]), _signed_pct(fav_m["mean_edge"]), _signed_pct(dog_m["mean_edge"])),
        ("Simulated flat-stake ROI", _signed_pct(all_m["roi"]), _signed_pct(fav_m["roi"]), _signed_pct(dog_m["roi"])),
    ]
    summary_rows = "\n".join(
        f"<tr><td>{label}</td><td>{all_v}</td><td>{fav_v}</td><td>{dog_v}</td></tr>"
        for label, all_v, fav_v, dog_v in rows
    )

    pick_rows = "\n".join(
        f"<tr><td>{p['prediction_id']}</td><td class=\"text\">{_escape(p['prediction_text'])}</td>"
        f"<td>{p['side']}</td><td>{p['bucket']}</td>"
        f"<td>{p['prob']:.3f}</td><td>{'correct' if p['outcome'] else 'incorrect'}</td>"
        f"<td>{p['edge']:+.3f}</td><td>{p['profit']:+.3f}</td></tr>"
        for p in sorted(picks, key=lambda p: (p["episode_date"], p["prediction_id"]))
    )
    def total_row(label, subset):
        return (
            f'\n<tr class="total"><td>{label} ({len(subset)} picks)</td><td></td><td></td>'
            f"<td></td><td></td><td></td><td>{sum(p['edge'] for p in subset):+.3f}</td>"
            f"<td>{sum(p['profit'] for p in subset):+.3f}</td></tr>"
        )

    # General-preferred set: for predictions with both an exact and a general pick
    # (two-market series), keep only the general (series-winner) pick and drop the
    # exact (game-count O/U) one. Single-market predictions are unaffected.
    sides_by_id = {}
    for p in picks:
        sides_by_id.setdefault(p["prediction_id"], set()).add(p["side"])
    general_preferred = [
        p for p in picks
        if not (p["side"] == "exact" and {"exact", "general"} <= sides_by_id[p["prediction_id"]])
    ]

    pick_rows += total_row("Total", picks)
    pick_rows += total_row("Total (general preferred)", general_preferred)

    chart_json = json.dumps(build_chart_data(picks, general_preferred))

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>{speaker} &mdash; betting value report</title>
<style>
  body {{ font-family: -apple-system, sans-serif; margin: 2rem; color: #222; }}
  table {{ border-collapse: collapse; margin-bottom: 2rem; }}
  th, td {{ border: 1px solid #ccc; padding: 0.4rem 0.8rem; text-align: right; }}
  th, td:first-child {{ text-align: left; }}
  td.text {{ text-align: left; max-width: 28rem; }}
  th {{ background: #f4f4f4; }}
  tr.total td {{ font-weight: bold; border-top: 2px solid #888; }}
  h2 {{ margin-top: 2.5rem; }}
  .chart {{ max-width: 760px; height: 320px; margin-bottom: 1rem; }}
  .note {{ color: #666; font-size: 0.85rem; max-width: 760px; }}
</style>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
</head>
<body>
<h1>{speaker} &mdash; betting value report</h1>
<p>{all_m["n"]} graded picks with market odds, {date_range}.</p>

<table>
<tr><th>Metric</th><th>All</th><th>Favorite picks</th><th>Underdog picks</th></tr>
{summary_rows}
</table>

<h2>Cumulative profit ($1 flat stake)</h2>
<div class="chart"><canvas id="cumulative"></canvas></div>
<p class="note">Running bankroll in pick (date) order. Vertical jumps are longshot wins;
long flat/declining stretches are drawdowns. A spiky line means the profit leans on a few
hits rather than a steady edge.</p>

<h2>Market probability vs. profit</h2>
<div class="chart"><canvas id="scatter"></canvas></div>
<p class="note">One dot per pick. Left of 0.5 = underdog picks, right = market favorites.
Green = win, red = loss. Shows where his profit concentrates across the favorite&ndash;underdog
spectrum.</p>

<h2>Calibration vs. market</h2>
<div class="chart"><canvas id="calibration"></canvas></div>
<p class="note">Per market-probability bucket: his actual hit rate vs. the market's mean
implied probability. Hit rate above the market bar = he beat the market's pricing in that
range. Small bucket counts (shown in labels) mean noisy bars.</p>

<h2>Picks</h2>
<table>
<tr><th>Prediction</th><th>Text</th><th>Market</th><th>Bucket</th><th>Market prob</th><th>Outcome</th><th>Edge</th><th>Profit</th></tr>
{pick_rows}
</table>

<script>
const DATA = {chart_json};

new Chart(document.getElementById('cumulative'), {{
  type: 'line',
  data: {{
    labels: DATA.cumulative.labels,
    datasets: [
      {{ label: 'All picks', data: DATA.cumulative.all, borderColor: '#1f77b4',
         backgroundColor: '#1f77b4', tension: 0, pointRadius: 2 }},
      {{ label: 'General preferred', data: DATA.cumulative.gp, borderColor: '#ff7f0e',
         backgroundColor: '#ff7f0e', tension: 0, pointRadius: 2 }},
    ],
  }},
  options: {{
    maintainAspectRatio: false,
    scales: {{ x: {{ title: {{ display: true, text: 'pick # (date order)' }} }},
              y: {{ title: {{ display: true, text: 'cumulative profit ($)' }} }} }},
    plugins: {{ tooltip: {{ callbacks: {{
      title: (items) => DATA.cumulative.pids[items[0].dataIndex] }} }} }},
  }},
}});

new Chart(document.getElementById('scatter'), {{
  type: 'scatter',
  data: {{ datasets: [
    {{ label: 'Win', data: DATA.scatter.win, backgroundColor: '#2ca02c' }},
    {{ label: 'Loss', data: DATA.scatter.loss, backgroundColor: '#d62728' }},
  ] }},
  options: {{
    maintainAspectRatio: false,
    scales: {{
      x: {{ min: 0, max: 1, title: {{ display: true, text: 'market probability of his pick (0.5 = even)' }} }},
      y: {{ title: {{ display: true, text: 'profit ($)' }} }},
    }},
    plugins: {{ tooltip: {{ callbacks: {{
      label: (c) => `${{c.raw.pid}}: prob ${{c.raw.x}}, profit ${{c.raw.y}}` }} }} }},
  }},
}});

new Chart(document.getElementById('calibration'), {{
  type: 'bar',
  data: {{
    labels: DATA.calibration.labels.map((l, i) => `${{l}} (n=${{DATA.calibration.counts[i]}})`),
    datasets: [
      {{ label: 'His hit rate', data: DATA.calibration.hit_rate, backgroundColor: '#1f77b4' }},
      {{ label: 'Market mean prob', data: DATA.calibration.market_prob, backgroundColor: '#aaa' }},
    ],
  }},
  options: {{
    maintainAspectRatio: false,
    scales: {{ y: {{ min: 0, max: 1, title: {{ display: true, text: 'probability' }} }} }},
  }},
}});
</script>
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--speaker", default="Draymond Green")
    args = parser.parse_args()

    rows = fetch_rows(args.speaker)
    picks = build_picks(rows)

    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text(render_html(args.speaker, picks))
    print(f"{len(picks)} picks -> {REPORT}")


if __name__ == "__main__":
    main()
