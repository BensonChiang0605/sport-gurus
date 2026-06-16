"""Reusable NBA game-result lookups, backed by nba_api.

`get_playoff_games(season)` returns a normalised list of game dicts that the
(future) prediction-grading pass can match claims against. Running this module
directly prints the playoff game list, preserving the original script behaviour.

    uv run scripts/nba_games.py [--season 2025-26]
"""

import argparse

import pandas as pd
from nba_api.stats.endpoints import leaguegamefinder


def get_playoff_games(season: str = "2025-26") -> list[dict]:
    """Return playoff games for a season as {date, winner, loser, win_score, lose_score}."""
    finder = leaguegamefinder.LeagueGameFinder(
        league_id_nullable="00",
        season_type_nullable="Playoffs",
        season_nullable=season,
    )
    df = finder.get_data_frames()[0]

    # 'vs.' isolates the home team's row, preventing duplicate match listings.
    games = df[df["MATCHUP"].str.contains(r"vs\.")].sort_values("GAME_DATE")

    results = []
    for _, row in games.iterrows():
        pts = int(row["PTS"])
        opp_pts = int(pts - row["PLUS_MINUS"]) if pd.notnull(row["PLUS_MINUS"]) else None
        home, away = row["MATCHUP"].split(" vs. ")
        if row["WL"] == "W":
            winner, loser, win_score, lose_score = home, away, pts, opp_pts
        else:
            winner, loser, win_score, lose_score = away, home, opp_pts, pts
        results.append({
            "date": row["GAME_DATE"],
            "winner": winner,
            "loser": loser,
            "win_score": win_score,
            "lose_score": lose_score,
        })
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--season", default="2025-26", help="e.g. 2025-26")
    args = parser.parse_args()

    games = get_playoff_games(args.season)
    print(f"--- Total {args.season} Playoff Games Found: {len(games)} ---\n")
    for g in games:
        print(f"[{g['date']}] {g['winner']} def. {g['loser']} "
              f"({g['win_score']}-{g['lose_score']})")


if __name__ == "__main__":
    main()
