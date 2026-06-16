"""Reusable NBA game-result lookups, backed by nba_api.

Design is game-list-first and season-type-agnostic: every result is a normalised
game dict tagged with `season_type` (regular/playin/playoff). Series are a *derived
view* over the playoff games only — regular-season games have no series, so the
game-level helpers (games_between / result_on / season_series) are what grade those.

Three layers:
  * fetch + cache   — get_games(), refresh_cache(), load_cache()
  * query helpers   — games_between, result_on, season_series (any season type)
                      did_win_series, series_length, game_by_number (playoffs only)
  * grading facts   — build_facts() emits the compact JSON the grader consumes

CLI:
    uv run scripts/nba_games.py                 # print postseason game list (default)
    uv run scripts/nba_games.py --write         # refresh game-data/nba-games.json
    uv run scripts/nba_games.py --facts         # print grading facts as JSON
    uv run scripts/nba_games.py --season-types "Regular Season"
"""

import argparse
import json
import pathlib

import pandas as pd
from nba_api.stats.endpoints import leaguegamefinder
from nba_api.stats.static import teams

CACHE_PATH = pathlib.Path("game-data/nba-games.json")
DEFAULT_SEASON = "2025-26"
# Postseason by default; pass "Regular Season" to include the full schedule.
DEFAULT_SEASON_TYPES = ("PlayIn", "Playoffs")

# nba_api's season_type label -> our compact tag.
_SEASON_TYPE_TAG = {
    "Regular Season": "regular",
    "PlayIn": "playin",
    "Playoffs": "playoff",
}


# --- team-name normalization ------------------------------------------------

def _build_name_index() -> dict[str, str]:
    """Map full_name / nickname / city / abbreviation (lowercased) -> abbreviation."""
    index: dict[str, str] = {}
    for t in teams.get_teams():
        abbr = t["abbreviation"]
        for key in (t["full_name"], t["nickname"], t["city"], abbr):
            index[key.lower()] = abbr
    return index


_NAME_INDEX = _build_name_index()


def to_abbrev(name: str) -> str:
    """Normalise a team reference ('Celtics', 'Boston Celtics', 'BOS') to its abbrev."""
    return _NAME_INDEX.get(name.strip().lower(), name.strip().upper())


# --- fetch + cache ----------------------------------------------------------

def get_games(season: str = DEFAULT_SEASON,
              season_types: tuple[str, ...] = DEFAULT_SEASON_TYPES) -> list[dict]:
    """Return normalised games for a season across the given season types.

    Each game: {date, winner, loser, win_score, lose_score, season, season_type}
    with winner/loser as 3-letter abbreviations. Sorted by date.
    """
    results: list[dict] = []
    for season_type in season_types:
        df = leaguegamefinder.LeagueGameFinder(
            league_id_nullable="00",
            season_type_nullable=season_type,
            season_nullable=season,
        ).get_data_frames()[0]

        # 'vs.' isolates the home team's row, preventing duplicate match listings.
        games = df[df["MATCHUP"].str.contains(r"vs\.")]
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
                "season": season,
                "season_type": _SEASON_TYPE_TAG.get(season_type, season_type.lower()),
            })

    results.sort(key=lambda g: g["date"])
    return results


def refresh_cache(path: pathlib.Path = CACHE_PATH,
                  seasons: tuple[str, ...] = (DEFAULT_SEASON,),
                  season_types: tuple[str, ...] = DEFAULT_SEASON_TYPES) -> list[dict]:
    """Fetch games for the given seasons and write them to the JSON cache."""
    games: list[dict] = []
    for season in seasons:
        games.extend(get_games(season, season_types))
    games.sort(key=lambda g: (g["season"], g["date"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(games, indent=2) + "\n")
    return games


def load_cache(path: pathlib.Path = CACHE_PATH) -> list[dict]:
    """Read the cached game list."""
    return json.loads(path.read_text())


# --- game-level query helpers (any season type) -----------------------------

def games_between(a: str, b: str, games: list[dict], season: str | None = None) -> list[dict]:
    """All games between two teams (order-independent), optionally filtered by season."""
    a, b = to_abbrev(a), to_abbrev(b)
    pair = {a, b}
    return [g for g in games
            if {g["winner"], g["loser"]} == pair
            and (season is None or g["season"] == season)]


def result_on(date: str, a: str, b: str, games: list[dict]) -> dict | None:
    """The game between two teams on a given date (YYYY-MM-DD), or None."""
    for g in games_between(a, b, games):
        if g["date"] == date:
            return g
    return None


def season_series(a: str, b: str, games: list[dict], season: str | None = None) -> dict:
    """Head-to-head record between two teams: {team: wins} plus the matching games."""
    a, b = to_abbrev(a), to_abbrev(b)
    matches = games_between(a, b, games, season)
    wins = {a: 0, b: 0}
    for g in matches:
        wins[g["winner"]] += 1
    return {"wins": wins, "games": matches}


# --- series-level derivation (playoffs only) --------------------------------

def build_series(games: list[dict]) -> list[dict]:
    """Group playoff games into series keyed by unordered team pair.

    Two teams meet in at most one series per season, so pair = series. Returns per
    series: teams, winner, loser, length, result string, and per-game detail with the
    running series record after each game.
    """
    playoff = [g for g in games if g["season_type"] == "playoff"]
    by_pair: dict[tuple, list[dict]] = {}
    for g in playoff:
        key = (g["season"], tuple(sorted((g["winner"], g["loser"]))))
        by_pair.setdefault(key, []).append(g)

    series_list: list[dict] = []
    for (season, pair), pair_games in by_pair.items():
        pair_games.sort(key=lambda g: g["date"])
        wins = {pair[0]: 0, pair[1]: 0}
        detail = []
        for i, g in enumerate(pair_games, start=1):
            wins[g["winner"]] += 1
            ordered = sorted(wins, key=lambda t: -wins[t])
            after = " ".join(f"{t} {wins[t]}" for t in ordered)
            detail.append({
                "game_no": i,
                "date": g["date"],
                "winner": g["winner"],
                "loser": g["loser"],
                "score": f"{g['win_score']}-{g['lose_score']}",
                "series_after": after,
            })
        winner = max(wins, key=lambda t: wins[t])
        loser = pair[0] if winner == pair[1] else pair[1]
        series_list.append({
            "season": season,
            "teams": list(pair),
            "winner": winner,
            "loser": loser,
            "length": len(pair_games),
            "result": f"{winner} beat {loser} {wins[winner]}-{wins[loser]}",
            "games": detail,
        })
    series_list.sort(key=lambda s: (s["season"], s["games"][0]["date"]))
    return series_list


def _find_series(a: str, b: str, games: list[dict]) -> dict | None:
    a, b = to_abbrev(a), to_abbrev(b)
    pair = {a, b}
    for s in build_series(games):
        if set(s["teams"]) == pair:
            return s
    return None


def did_win_series(a: str, b: str, games: list[dict]) -> str | None:
    """Winner abbrev of the playoff series between a and b, or None if no such series."""
    s = _find_series(a, b, games)
    return s["winner"] if s else None


def series_length(a: str, b: str, games: list[dict]) -> int | None:
    """Number of games in the playoff series between a and b, or None."""
    s = _find_series(a, b, games)
    return s["length"] if s else None


def game_by_number(a: str, b: str, n: int, games: list[dict]) -> dict | None:
    """Game number n of the playoff series between a and b, or None."""
    s = _find_series(a, b, games)
    if not s:
        return None
    return next((g for g in s["games"] if g["game_no"] == n), None)


# --- grading facts ----------------------------------------------------------

def build_facts(games: list[dict]) -> dict:
    """Compact fact bundle for the grader: derived series, play-in, and raw games."""
    return {
        "series": build_series(games),
        "playin": [
            {"date": g["date"], "winner": g["winner"], "loser": g["loser"],
             "score": f"{g['win_score']}-{g['lose_score']}"}
            for g in games if g["season_type"] == "playin"
        ],
        "games": games,
    }


# --- CLI --------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--season", default=DEFAULT_SEASON, help="e.g. 2025-26")
    parser.add_argument("--season-types", nargs="+", default=list(DEFAULT_SEASON_TYPES),
                        help='nba_api labels, e.g. "Regular Season" PlayIn Playoffs')
    parser.add_argument("--write", action="store_true",
                        help=f"refresh the cache at {CACHE_PATH}")
    parser.add_argument("--facts", action="store_true",
                        help="print grading facts (series/playin/games) as JSON")
    args = parser.parse_args()
    season_types = tuple(args.season_types)

    if args.write:
        games = refresh_cache(seasons=(args.season,), season_types=season_types)
        print(f"Wrote {len(games)} games to {CACHE_PATH}")
        return

    games = get_games(args.season, season_types)

    if args.facts:
        print(json.dumps(build_facts(games), indent=2))
        return

    print(f"--- {args.season} games found: {len(games)} ---\n")
    for g in games:
        print(f"[{g['date']}] ({g['season_type']}) {g['winner']} def. {g['loser']} "
              f"({g['win_score']}-{g['lose_score']})")


if __name__ == "__main__":
    main()
