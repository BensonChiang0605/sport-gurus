"""Polymarket pregame odds as a market benchmark for NBA predictions.

For each `game`/`series` prediction we attach the prediction-market-implied
probability *at the start of the game/series* — so the leaderboard can later ask
"did the podcaster beat the market?" rather than just "were they right?". Fetching
odds is pure data retrieval (no judgement), so it lives here in Python, per the repo
rule that deterministic work lives in shell/Python.

Two public, unauthenticated Polymarket hosts are used:
  * Gamma  (gamma-api.polymarket.com)  — market/event discovery + metadata.
  * CLOB   (clob.polymarket.com)       — price history (the pregame probability).

Note: a *closed* market's `outcomePrices` is the resolved result (["1","0"]), NOT the
pregame odds. The benchmark is read from the CLOB price trajectory: the last trade
at-or-before the start instant.

By default the snapshot is the *pregame* instant (game tip-off, or just before a series'
first game). The grader path instead snapshots *as-of the episode's publish datetime* —
what the market thought when the prediction was actually made — and only falls back to the
pregame instant when no price exists at episode time (the market hadn't opened yet, or the
episode aired after the event). See odds_for_game/series_odds' `as_of`.

Cache model — lazy + immutable. A price at a *past instant* never changes, so the cache
(game-data/polymarket-odds.json) is append-only: a cache hit returns with no network call;
a cache miss fetches, writes, and returns. The key is the market slug plus the snapshot
instant: pregame entries keep the bare `{slug}` key; episode-time entries use the composite
`{slug}@{iso}` key so two episodes predicting the same game don't collide. A not-yet-started
market (no price at the instant) is returned empty and left *uncached* so a later run
retries. There is therefore no refresh step in the grade loop; `--refresh` exists only to
pre-warm or rebuild the cache and is not part of the automated loop.

Three layers:
  * fetch        — _get_json, fetch_game_markets, fetch_series_markets, start_prob
  * cache        — load_odds_cache, save_odds_entry
  * query        — odds_for_game, series_odds, odds_for_text (grader entry point)

CLI:
    uv run scripts/polymarket_odds.py --game "GSW LAC" --date 2026-04-15
    uv run scripts/polymarket_odds.py --series "Knicks Spurs"
    uv run scripts/polymarket_odds.py --odds-for-text "<text>" --category game
    uv run scripts/polymarket_odds.py --report                  # coverage audit (DB)
    uv run scripts/polymarket_odds.py --refresh [--year 2026]   # OPTIONAL pre-warm
"""

import argparse
import datetime
import json
import pathlib
import re
import sqlite3
import urllib.error
import urllib.request

from nba_games import (
    DEFAULT_SEASON,
    build_series,
    games_between,
    load_cache,
    teams_in_text,
    to_abbrev,
)

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
ODDS_CACHE_PATH = pathlib.Path("game-data/polymarket-odds.json")
PREDICTIONS_DB_PATH = pathlib.Path("predictions.db")

# A point-margin claim ("win by 40 points") has no Polymarket benchmark — there is
# no margin market — so it is expected to stay empty, not a coverage gap.
_MARGIN_RE = re.compile(r"\bby\s+\d+\b", re.IGNORECASE)
_USER_AGENT = "sport-gurus/1.0 (+https://github.com/; NBA prediction benchmark)"

# Series who-wins / total-games slugs use fuller team forms (cavaliers, timberwolves,
# trail-blazers) that to_abbrev() resolves once hyphens become spaces. This small map
# is a fallback for the short nicknames that still miss the nba_api name index.
_SLUG_ALIASES = {
    "cavs": "CLE", "wolves": "MIN", "blazers": "POR", "sixers": "PHI",
    "76ers": "PHI", "trail blazers": "POR", "knicks": "NYK",
}


def playoffs_year(season: str = DEFAULT_SEASON) -> int:
    """Calendar year the playoffs fall in: '2025-26' -> 2026 (the tag slug's year)."""
    start, end = season.split("-")
    return int(start[:2] + end)


# --- HTTP -------------------------------------------------------------------

def _get_json(url: str):
    """GET JSON with the required User-Agent. One retry, ~10s timeout, fail soft.

    Polymarket returns HTTP 403 to requests with no User-Agent. On any network/parse
    error this returns None so grading never blocks or crashes on the network.
    """
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode())
        except (urllib.error.URLError, TimeoutError, ValueError, OSError):
            if attempt == 0:
                continue
            return None
    return None


def _slug_team(token: str) -> str:
    """Resolve a team name parsed out of a slug to its canonical 3-letter abbrev."""
    name = token.replace("-", " ").strip()
    return _SLUG_ALIASES.get(name.lower(), to_abbrev(name))


def _parse_start_ts(game_start_time: str) -> int | None:
    """Parse Gamma's gameStartTime ('2026-06-09 00:30:00+00') to a UTC epoch."""
    if not game_start_time:
        return None
    text = game_start_time.strip()
    if text.endswith("+00"):
        text = text[:-3] + "+0000"
    try:
        return int(datetime.datetime.strptime(text, "%Y-%m-%d %H:%M:%S%z").timestamp())
    except ValueError:
        return None


def _iso(ts: int) -> str:
    """UTC epoch seconds -> 'YYYY-MM-DDTHH:MM:SSZ'."""
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _as_of_ts(episode_datetime: str) -> int | None:
    """Episode publish instant ('2026-05-19T06:30:03+08:00') as a UTC epoch, or None.

    None when the field is empty/unparseable so the caller degrades to the pregame
    (event-start) snapshot — the historical behaviour.
    """
    if not episode_datetime:
        return None
    try:
        return int(datetime.datetime.fromisoformat(episode_datetime).timestamp())
    except ValueError:
        return None


# At episode time a market may be younger / less liquid than at tip-off, so look back
# further than the tip-off default when snapshotting as-of the episode (see start_prob).
# Capped at 14: the CLOB prices-history endpoint rejects startTs/endTs spans beyond ~15
# days (returns no data regardless of fidelity), which would otherwise force every
# episode snapshot to spuriously fall back to pregame.
_AS_OF_LOOKBACK_DAYS = 14


def _us_date(start_ts: int) -> str:
    """US calendar date of a tip-off, derived from its UTC start instant.

    A US evening game rolls past midnight UTC, so subtract enough to land on the US
    game day; using -1 day from the UTC instant and taking the date matches the date
    used in nba-games.json and the game slug (nba-{away}-{home}-{YYYY-MM-DD}).
    """
    dt = datetime.datetime.fromtimestamp(start_ts, datetime.timezone.utc) - datetime.timedelta(days=1)
    return dt.strftime("%Y-%m-%d")


# --- fetch ------------------------------------------------------------------

def start_prob(token_id: str, start_ts: int, lookback_days: int = 7) -> tuple[float, int] | None:
    """Implied probability at-or-before start_ts from the CLOB price history.

    Returns (prob, captured_ts) for the last trade with t <= start_ts, or None if no
    pre-start price exists yet (market not started — caller must not cache it).

    `lookback_days` sizes the pre-window. The default (7) is tuned for tip-off liquidity;
    the episode-datetime path widens it because a market read days before the game may
    have only early, sparse trades.
    """
    if not token_id or not start_ts:
        return None
    lo = start_ts - lookback_days * 86400
    url = f"{CLOB}/prices-history?market={token_id}&startTs={lo}&endTs={start_ts}&fidelity=10"
    data = _get_json(url)
    if not data:
        return None
    history = data.get("history", [])
    pre = [p for p in history if p.get("t", 0) <= start_ts]
    if not pre:
        return None
    last = pre[-1]
    return float(last["p"]), int(last["t"])


def _fetch_market_by_slug(slug: str) -> dict | None:
    """Fetch a single closed Gamma market by exact slug, or None."""
    data = _get_json(f"{GAMMA}/markets?slug={slug}&closed=true")
    if data and isinstance(data, list):
        return data[0]
    return None


def fetch_game_markets(year: int) -> list[dict]:
    """All closed single-game moneyline markets for a playoffs year (paginated).

    Each normalised to {slug, date, teams:[A,B], tokens:{A:tid,B:tid}, start_ts}.
    Used by --refresh to pre-warm the cache; the lazy per-game path uses direct slug
    lookup instead (see odds_for_game).
    """
    out: list[dict] = []
    offset, limit = 0, 100
    while True:
        url = (f"{GAMMA}/markets?tag_id=745&sports_market_types=moneyline&closed=true"
               f"&order=gameStartTime&ascending=false&limit={limit}&offset={offset}")
        batch = _get_json(url)
        if not batch:
            break
        for m in batch:
            norm = _normalize_game_market(m)
            if norm and norm["date"].startswith(str(year)):
                out.append(norm)
        if len(batch) < limit:
            break
        offset += limit
    return out


def _normalize_game_market(m: dict) -> dict | None:
    """Gamma game market -> {slug, date, teams, tokens, start_ts}, or None if unusable."""
    slug = m.get("slug", "")
    parts = slug.split("-")
    # nba-{away}-{home}-{YYYY}-{MM}-{DD}
    if len(parts) < 6 or parts[0] != "nba":
        return None
    away, home = parts[1].upper(), parts[2].upper()
    start_ts = _parse_start_ts(m.get("gameStartTime", ""))
    if not start_ts:
        return None
    try:
        tokens = json.loads(m.get("clobTokenIds", "[]"))
        outcomes = json.loads(m.get("outcomes", "[]"))
    except ValueError:
        return None
    if len(tokens) != 2 or len(outcomes) != 2:
        return None
    # outcomes are nicknames index-aligned with tokens; map each to an abbrev.
    tok_by_abbrev = {to_abbrev(o): t for o, t in zip(outcomes, tokens)}
    if away not in tok_by_abbrev or home not in tok_by_abbrev:
        return None
    return {
        "slug": slug,
        "date": _us_date(start_ts),
        "teams": [away, home],
        "tokens": tok_by_abbrev,
        "start_ts": start_ts,
    }


def fetch_series_markets(year: int) -> dict:
    """Closed playoffs events for a year, bucketed into who-wins-series + total-games.

    Returns {"winner": [...], "total_games": [...]}, each entry normalised to
    {slug, teams:[A,B], tokens:{...}, line (total_games only)}. Teams are parsed from
    the slug (not `outcomes`), per the matching gotcha.
    """
    events = _get_json(f"{GAMMA}/events?tag_slug={year}-nba-playoffs&closed=true&limit=500&offset=0")
    winner, total = [], []
    if not events:
        return {"winner": winner, "total_games": total}
    win_prefix = "nba-playoffs-who-will-win-series-"
    for e in events:
        slug = e.get("slug", "")
        markets = e.get("markets", [])
        if not markets:
            continue
        if slug.startswith(win_prefix):
            pair = slug[len(win_prefix):]
            entry = _normalize_series_market(markets[0], pair)
            if entry:
                winner.append(entry)
        elif slug.startswith("nba-playoffs-") and "-total-games-ou-" in slug:
            body, ou = slug[len("nba-playoffs-"):].split("-total-games-ou-")
            entry = _normalize_series_market(markets[0], body)
            if entry:
                entry["line"] = float(ou.replace("pt", "."))
                total.append(entry)
    return {"winner": winner, "total_games": total}


def _normalize_series_market(m: dict, pair_text: str) -> dict | None:
    """Series market + the '{a}-vs-{b}' slug body -> {slug, teams, tokens}, or None."""
    if "-vs-" not in pair_text:
        return None
    a_raw, b_raw = pair_text.split("-vs-", 1)
    a, b = _slug_team(a_raw), _slug_team(b_raw)
    try:
        tokens = json.loads(m.get("clobTokenIds", "[]"))
        outcomes = json.loads(m.get("outcomes", "[]"))
    except ValueError:
        return None
    if len(tokens) != 2 or len(outcomes) != 2:
        return None
    return {
        "slug": m.get("slug", ""),
        "teams": [a, b],
        # outcomes index-aligned with tokens; for the winner market they are the two
        # team nicknames, for total-games they are ["Over N", "Under N"].
        "tokens": dict(zip(outcomes, tokens)),
    }


# Conference Finals / NBA Finals winner odds are not published as a per-matchup
# "who-will-win-series-{a}-vs-{b}" market like rounds 1-2. Instead each round has one
# event with a binary Yes/No market per team still alive. Tried in order; the first
# event containing both teams wins.
_ROUND_CHAMPION_EVENT_SLUGS = [
    "nba-playoffs-eastern-conference-champion",
    "nba-playoffs-western-conference-champion",
]
_CHAMPION_QUESTION_RE = re.compile(r"^Will the (.+?) win the", re.IGNORECASE)


def _champion_event_team_tokens(slug: str) -> dict[str, str]:
    """Champion-event slug -> {team abbrev: clob token id for its 'Yes' outcome}."""
    data = _get_json(f"{GAMMA}/events?slug={slug}&closed=true")
    if not data:
        return {}
    out: dict[str, str] = {}
    for m in data[0].get("markets", []):
        match = _CHAMPION_QUESTION_RE.match(m.get("question", ""))
        if not match:
            continue
        abbrev = to_abbrev(match.group(1))
        try:
            tokens = json.loads(m.get("clobTokenIds", "[]"))
            outcomes = json.loads(m.get("outcomes", "[]"))
        except ValueError:
            continue
        yes_idx = next((i for i, o in enumerate(outcomes) if o.lower() == "yes"), None)
        if yes_idx is None or yes_idx >= len(tokens):
            continue
        out[abbrev] = tokens[yes_idx]
    return out


def _round_winner_probs(team_a: str, team_b: str, snap: int, year: int,
                        lookback: int = 7) -> dict | None:
    """Winner odds for a Conference Finals / NBA Finals matchup, or None if no event
    has both teams (e.g. the matchup hasn't reached that round on Polymarket yet).

    Each team's Yes-price is its own independent binary market, so the pair is
    renormalized to sum to 1 rather than trusted as-is.
    """
    for slug in [*_ROUND_CHAMPION_EVENT_SLUGS, f"{year}-nba-champion"]:
        tokens = _champion_event_team_tokens(slug)
        if team_a not in tokens or team_b not in tokens:
            continue
        sp_a = start_prob(tokens[team_a], snap, lookback)
        sp_b = start_prob(tokens[team_b], snap, lookback)
        if sp_a is None or sp_b is None:
            return None
        total = sp_a[0] + sp_b[0]
        if total <= 0:
            return None
        probs = {team_a: round(sp_a[0] / total, 4), team_b: round(sp_b[0] / total, 4)}
        return {"probs": probs, "favored": max(probs, key=probs.get),
                "captured_ts": _iso(max(sp_a[1], sp_b[1])), "slug": slug}
    return {}


# --- cache ------------------------------------------------------------------

def load_odds_cache(path: pathlib.Path = ODDS_CACHE_PATH) -> dict:
    """Read the lazy odds cache (slug -> entry), or {} if absent."""
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def save_odds_entry(slug: str, entry: dict, path: pathlib.Path = ODDS_CACHE_PATH) -> None:
    """Write one entry into the odds cache, keyed by its source slug."""
    cache = load_odds_cache(path)
    cache[slug] = entry
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=2, sort_keys=True) + "\n")


# --- query ------------------------------------------------------------------

def _snapshot_game(norm: dict, snap_ts: int, lookback: int) -> dict | None:
    """Snapshot a normalized game market at snap_ts. Entry dict, or None when any side
    has no price at-or-before snap_ts (market not open yet at that instant)."""
    probs, captured = {}, None
    for team, token in norm["tokens"].items():
        sp = start_prob(token, snap_ts, lookback)
        if sp is None:
            return None
        probs[team], captured = round(sp[0], 4), sp[1]
    return {"kind": "game", "date": norm["date"], "probs": probs,
            "favored": max(probs, key=probs.get), "captured_ts": _iso(captured)}


def odds_for_game(team_a: str, team_b: str, date: str, as_of: int | None = None) -> dict:
    """Market odds for a single game on a date (YYYY-MM-DD), cache-first.

    Returns {kind, date, probs:{A,B}, favored, source_slug, captured_ts} or {} when no
    market matches / no pre-start price exists yet. The pair is matched order-free by
    trying both slug orderings (nba-{a}-{b}-{date} and nba-{b}-{a}-{date}).

    With `as_of` (a UTC epoch — the episode's publish instant) the snapshot is taken at
    the episode time instead of tip-off, capped at tip-off (a game claim is never read
    after its own start). Episode snapshots are cached under the composite key
    `{slug}@{iso}`; if no price exists at the episode instant the function falls back to
    the pregame (tip-off) snapshot under the bare `{slug}` key — the historical default.
    """
    a, b = to_abbrev(team_a), to_abbrev(team_b)
    candidates = [f"nba-{a.lower()}-{b.lower()}-{date}", f"nba-{b.lower()}-{a.lower()}-{date}"]
    cache = load_odds_cache()

    # Episode-time snapshot (composite key), cache-first. as_of >= tip-off, or no price at
    # the episode instant, falls through to the pregame path below.
    if as_of is not None:
        as_of_iso = _iso(as_of)
        for slug in candidates:
            ckey = f"{slug}@{as_of_iso}"
            if ckey in cache:
                return {"source_slug": slug, **cache[ckey]}
        for slug in candidates:
            market = _fetch_market_by_slug(slug)
            if not market:
                continue
            norm = _normalize_game_market(market)
            if not norm or {a, b} != set(norm["teams"]):
                continue
            if as_of < norm["start_ts"]:
                entry = _snapshot_game(norm, as_of, _AS_OF_LOOKBACK_DAYS)
                if entry:
                    save_odds_entry(f"{slug}@{as_of_iso}", entry)
                    return {"source_slug": slug, **entry}
            break  # matched market, but no usable episode price -> pregame fallback

    # Pregame (tip-off) snapshot — the historical default and the as_of fallback.
    for slug in candidates:
        if slug in cache:
            return {"source_slug": slug, **cache[slug]}
    for slug in candidates:
        market = _fetch_market_by_slug(slug)
        if not market:
            continue
        norm = _normalize_game_market(market)
        if not norm or {a, b} != set(norm["teams"]):
            continue
        entry = _snapshot_game(norm, norm["start_ts"], 7)
        if entry is None:
            return {}  # not started yet — leave uncached, retry later
        save_odds_entry(slug, entry)
        return {"source_slug": slug, **entry}
    return {}


def _series_snapshot_ts(team_a: str, team_b: str, games: list[dict]) -> int | None:
    """Pre-tip instant of a series' first game (date 23:30 UTC), from nba-games.json.

    Series markets have no gameStartTime, so the snapshot is pinned to just before the
    first game; the series-winner price is flat in this pre-series window. Returns None
    if the series isn't in the cache yet (so the caller leaves it uncached and retries).
    """
    pair_games = sorted((g for g in games_between(team_a, team_b, games)),
                        key=lambda g: g["date"])
    if not pair_games:
        return None
    d = datetime.datetime.strptime(pair_games[0]["date"], "%Y-%m-%d")
    return int(d.replace(hour=23, minute=30, tzinfo=datetime.timezone.utc).timestamp())


def _series_at(team_a: str, team_b: str, year: int, markets: dict, snap: int,
               key_suffix: str, lookback: int) -> dict | None:
    """Series winner (+ optional total-games) odds at instant `snap`, or None when the
    winner price isn't available there (caller falls back to the pregame instant).

    Cache entries are keyed by `{slug}{key_suffix}` so an episode-time snapshot ("@{iso}")
    never collides with the pregame ("") entry; `source_slug` stays the clean slug so the
    LLM routes the right market name regardless of which instant was used.
    """
    cache = load_odds_cache()
    win = next((m for m in markets["winner"] if {team_a, team_b} == set(m["teams"])), None)
    if win:
        win_slug = win["slug"]
        win_key = win_slug + key_suffix
        if win_key in cache:
            win_entry = cache[win_key]
        else:
            probs, captured = {}, None
            for outcome, token in win["tokens"].items():
                sp = start_prob(token, snap, lookback)
                if sp is None:
                    return None
                probs[to_abbrev(outcome)], captured = round(sp[0], 4), sp[1]
            win_entry = {"kind": "series-winner", "probs": probs,
                         "favored": max(probs, key=probs.get), "captured_ts": _iso(captured)}
            save_odds_entry(win_key, win_entry)
    else:
        # Conference Finals / NBA Finals: no per-matchup slug, fall back to the
        # round's multi-team champion event (see _round_winner_probs).
        win_slug = next((s for s in [*_ROUND_CHAMPION_EVENT_SLUGS, f"{year}-nba-champion"]
                         if (s + key_suffix) in cache
                         and {team_a, team_b} <= set(cache[s + key_suffix].get("probs", {}))),
                        None)
        if win_slug:
            win_entry = cache[win_slug + key_suffix]
        else:
            fallback = _round_winner_probs(team_a, team_b, snap, year, lookback)
            if not fallback:
                return None
            win_slug = fallback.pop("slug")
            win_entry = {"kind": "series-winner", **fallback}
            save_odds_entry(win_slug + key_suffix, win_entry)

    result = {
        "winner": {"probs": win_entry["probs"], "favored": win_entry["favored"],
                   "source_slug": win_slug},
        "captured_ts": win_entry["captured_ts"],
    }

    # Total-games O/U market — optional; include if present.
    tot = next((m for m in markets["total_games"] if {team_a, team_b} == set(m["teams"])), None)
    if tot:
        tot_key = tot["slug"] + key_suffix
        if tot_key in cache:
            tot_entry = cache[tot_key]
        else:
            over_token = next((t for o, t in tot["tokens"].items() if o.lower().startswith("over")), None)
            sp = start_prob(over_token, snap, lookback)
            tot_entry = None
            if sp is not None:
                tot_entry = {"kind": "series-total-games", "line": tot["line"],
                             "over_prob": round(sp[0], 4), "captured_ts": _iso(sp[1])}
                save_odds_entry(tot_key, tot_entry)
        if tot_entry:
            result["total_games"] = {"line": tot_entry["line"],
                                     "over_prob": tot_entry["over_prob"],
                                     "under_prob": round(1 - tot_entry["over_prob"], 4),
                                     "source_slug": tot["slug"]}
    return result


def series_odds(team_a: str, team_b: str, year: int, games: list[dict] | None = None,
                as_of: int | None = None) -> dict:
    """Who-wins-series + total-games O/U odds for a matchup, cache-first.

    Returns {winner:{probs:{A,B}, favored, source_slug},
             total_games:{line, over_prob, under_prob, source_slug}, captured_ts} or {}
    when no market matches / the series can't be pinned to a start yet.

    With `as_of` (a UTC epoch — the episode's publish instant) the snapshot is taken at
    the episode time rather than just before the first game. A series is never capped: a
    mid-series prediction is legitimately timed, and the market then reflects games already
    played. If no winner price exists at the episode instant the function falls back to the
    pregame (first-game) snapshot — the historical default.
    """
    a, b = to_abbrev(team_a), to_abbrev(team_b)
    games = load_cache() if games is None else games
    pregame_snap = _series_snapshot_ts(a, b, games)
    if not pregame_snap:
        return {}

    markets = fetch_series_markets(year)
    if as_of is not None:
        result = _series_at(a, b, year, markets, as_of, f"@{_iso(as_of)}", _AS_OF_LOOKBACK_DAYS)
        if result:
            return result
    return _series_at(a, b, year, markets, pregame_snap, "", 7) or {}


def _infer_opponent(team: str, prediction_text: str, games: list[dict]) -> str | None:
    """Opponent for a prediction that names only `team`, derived from the playoff
    bracket in nba-games.json, or None when it can't be pinned unambiguously.

    Predictions like "the Knicks will win the NBA Finals" name only the subject team,
    so the matchup must be recovered from the cache. Two safe cases:
      * "Finals"/"championship" text (not "conference finals") -> the team's last
        playoff series (the Finals is chronologically last for a finalist).
      * the team has exactly one playoff series so far -> that one opponent.
    Anything else is ambiguous (a team plays several series) -> None.
    """
    team = to_abbrev(team)
    team_series = [s for s in build_series(games) if team in s["teams"]]
    if not team_series:
        return None
    text = prediction_text.lower()
    finals = ("final" in text or "championship" in text) and "conference" not in text
    chosen = None
    if finals:
        chosen = team_series[-1]  # build_series is sorted by date; Finals is last
    elif len(team_series) == 1:
        chosen = team_series[0]
    if not chosen:
        return None
    return next(t for t in chosen["teams"] if t != team)


def _resolve_matchup(prediction_text: str, games: list[dict]) -> tuple[str, str] | None:
    """The two-team matchup a prediction is about, or None when it can't be pinned.

    Prefers the two teams named in the text; falls back to inferring the opponent
    when only one team is named (see _infer_opponent).
    """
    abbrevs = teams_in_text(prediction_text)
    if len(abbrevs) == 2:
        a, b = sorted(abbrevs)
        return a, b
    if len(abbrevs) == 1:
        team = next(iter(abbrevs))
        opp = _infer_opponent(team, prediction_text, games)
        if opp:
            return tuple(sorted((team, opp)))
    return None


def odds_for_text(prediction_text: str, category: str, year: int | None = None,
                  as_of: int | None = None) -> dict:
    """Grader entry point: market odds for the matchup named in a prediction's text.

    `series` -> series_odds; `game` -> pin the concrete game date via nba-games.json,
    then odds_for_game. The matchup is the two teams named in the text, or (when only
    one is named) the subject team plus an inferred opponent (see _resolve_matchup).
    A game is pinned when the teams meet once in the cache, or when the text names a
    "Game N" (playoff series meet repeatedly, but the claim names which game in
    chronological order). Returns {} when no market matches or it can't be pinned.

    `as_of` (a UTC epoch — the episode's publish instant) snapshots the market at the
    time the prediction was made, falling back to the pregame snapshot when no price
    exists then. Omit it (None) for the pure pregame benchmark (e.g. the coverage report).
    """
    year = playoffs_year() if year is None else year
    games = load_cache()
    matchup = _resolve_matchup(prediction_text, games)
    if not matchup:
        return {}
    a, b = matchup

    if category == "series":
        return series_odds(a, b, year, games, as_of)

    if category == "game":
        meetings = sorted(games_between(a, b, games), key=lambda g: g["date"])
        game_num_match = re.search(r"\bGame\s+(\d+)\b", prediction_text, re.IGNORECASE)
        if game_num_match:
            # Playoff series: teams meet multiple times, but the claim names which
            # game in the series, so pin it by chronological order instead of
            # requiring a single meeting.
            n = int(game_num_match.group(1))
            if n < 1 or n > len(meetings):
                return {}
            return odds_for_game(a, b, meetings[n - 1]["date"], as_of)
        if len(meetings) != 1:
            return {}  # can't pin a single game -> leave empty rather than guess
        return odds_for_game(a, b, meetings[0]["date"], as_of)

    return {}


# --- coverage report --------------------------------------------------------

def classify_missing(prediction_text: str, category: str, year: int | None = None) -> str:
    """Why a game/series prediction has no market odds — turns a silent empty into a
    labelled reason so a coverage gap (a bug) is distinguishable from an expected miss.

    Returns one of:
      * "MISSED-FILL"   — odds *are* available but the field is empty (a real gap/bug)
      * "no-market"     — matchup pins to a real NBA game/series but Polymarket has none
                          (or no pregame price captured yet)
      * "margin-claim"  — a point-margin claim; no market by design
      * "no-NBA-matchup"— text names no resolvable NBA matchup (e.g. NCAA, one vague team)
    Callers pass only rows whose stored market_prob is empty.
    """
    if _MARGIN_RE.search(prediction_text):
        return "margin-claim"
    if _resolve_matchup(prediction_text, load_cache()) is None:
        return "no-NBA-matchup"
    return "MISSED-FILL" if odds_for_text(prediction_text, category, year) else "no-market"


def _report(year: int) -> None:
    """Print a coverage audit of every game/series prediction missing market odds,
    grouped by reason. MISSED-FILL rows are real gaps; the rest are expected.
    """
    if not PREDICTIONS_DB_PATH.exists():
        print(f"No {PREDICTIONS_DB_PATH} — run ralph/sync.py first.")
        return
    con = sqlite3.connect(f"file:{PREDICTIONS_DB_PATH}?mode=ro", uri=True)
    try:
        # "Missing odds" = no benchmark in *any* field. A series is still covered when
        # only the exact triple is empty (e.g. a game-count claim with no total_games
        # market) as long as market_prob_general carries the series-winner odds — so
        # require both the exact and general probability to be empty.
        rows = con.execute(
            "SELECT prediction_id, category, prediction_text FROM predictions "
            "WHERE category IN ('game','series') "
            "AND (market_prob='' OR market_prob IS NULL) "
            "AND (market_prob_general='' OR market_prob_general IS NULL) "
            "ORDER BY prediction_id"
        ).fetchall()
    finally:
        con.close()

    buckets: dict[str, list[tuple[str, str]]] = {}
    for pid, category, text in rows:
        reason = classify_missing(text, category, year)
        buckets.setdefault(reason, []).append((pid, text))

    total = sum(len(v) for v in buckets.values())
    print(f"Game/series predictions missing market odds: {total}\n")
    # MISSED-FILL first — it's the only bucket that needs action.
    for reason in ["MISSED-FILL", "no-market", "margin-claim", "no-NBA-matchup"]:
        items = buckets.get(reason, [])
        if not items:
            continue
        flag = "  <-- ACTION: odds exist, re-run fill-odds" if reason == "MISSED-FILL" else ""
        print(f"[{reason}] {len(items)}{flag}")
        for pid, text in items:
            print(f"    {pid}  {text}")
        print()
    if not buckets.get("MISSED-FILL"):
        print("No coverage gaps: every empty prediction has a known, expected reason.")


# --- CLI --------------------------------------------------------------------

def _refresh(year: int) -> None:
    """Pre-warm the whole season's cache. OPTIONAL — not part of the grade loop."""
    games = load_cache()
    n = 0
    for m in fetch_game_markets(year):
        if odds_for_game(m["teams"][0], m["teams"][1], m["date"]):
            n += 1
    series = fetch_series_markets(year)
    for m in series["winner"]:
        if series_odds(m["teams"][0], m["teams"][1], year, games):
            n += 1
    print(f"Pre-warmed {n} markets into {ODDS_CACHE_PATH}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--game", metavar='"A B"', help='two teams, e.g. "GSW LAC"')
    parser.add_argument("--date", help="game date YYYY-MM-DD (required with --game)")
    parser.add_argument("--series", metavar='"A B"', help='two teams, e.g. "Knicks Spurs"')
    parser.add_argument("--odds-for-text", metavar="TEXT", help="prediction text (grader path)")
    parser.add_argument("--category", choices=["game", "series"], help="with --odds-for-text")
    parser.add_argument("--as-of", metavar="ISO8601",
                        help="episode publish datetime; snapshot the market as-of then, "
                             "falling back to pregame (default: pregame)")
    parser.add_argument("--year", type=int, default=playoffs_year(),
                        help="playoffs calendar year, e.g. 2026")
    parser.add_argument("--refresh", action="store_true",
                        help="OPTIONAL pre-warm the whole season's cache")
    parser.add_argument("--report", action="store_true",
                        help="audit which game/series predictions are missing odds and why")
    args = parser.parse_args()
    as_of = _as_of_ts(args.as_of) if args.as_of else None

    if args.report:
        _report(args.year)
        return
    if args.refresh:
        _refresh(args.year)
        return
    if args.game:
        if not args.date:
            parser.error("--game requires --date")
        a, b = args.game.split()
        print(json.dumps(odds_for_game(a, b, args.date, as_of), indent=2))
        return
    if args.series:
        a, b = args.series.split()
        print(json.dumps(series_odds(a, b, args.year, as_of=as_of), indent=2))
        return
    if args.odds_for_text:
        if not args.category:
            parser.error("--odds-for-text requires --category")
        print(json.dumps(
            odds_for_text(args.odds_for_text, args.category, args.year, as_of), indent=2))
        return
    parser.error("nothing to do — pass --game, --series, --odds-for-text, --report, or --refresh")


if __name__ == "__main__":
    main()
