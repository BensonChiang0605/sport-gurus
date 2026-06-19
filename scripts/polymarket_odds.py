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

Cache model — lazy + immutable. Pregame odds for a finished game/series never change
once it has tipped off, so the cache (game-data/polymarket-odds.json) is append-only:
a cache hit returns with no network call; a cache miss fetches, writes, and returns.
A not-yet-started market (no pre-start price) is returned empty and left *uncached* so
a later run retries. There is therefore no refresh step in the grade loop; `--refresh`
exists only to pre-warm or rebuild the cache and is not part of the automated loop.

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


def _us_date(start_ts: int) -> str:
    """US calendar date of a tip-off, derived from its UTC start instant.

    A US evening game rolls past midnight UTC, so subtract enough to land on the US
    game day; using -1 day from the UTC instant and taking the date matches the date
    used in nba-games.json and the game slug (nba-{away}-{home}-{YYYY-MM-DD}).
    """
    dt = datetime.datetime.fromtimestamp(start_ts, datetime.timezone.utc) - datetime.timedelta(days=1)
    return dt.strftime("%Y-%m-%d")


# --- fetch ------------------------------------------------------------------

def start_prob(token_id: str, start_ts: int) -> tuple[float, int] | None:
    """Implied probability at-or-before start_ts from the CLOB price history.

    Returns (prob, captured_ts) for the last trade with t <= start_ts, or None if no
    pre-start price exists yet (market not started — caller must not cache it).
    """
    if not token_id or not start_ts:
        return None
    lo = start_ts - 7 * 86400
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


def _round_winner_probs(team_a: str, team_b: str, snap: int, year: int) -> dict | None:
    """Winner odds for a Conference Finals / NBA Finals matchup, or None if no event
    has both teams (e.g. the matchup hasn't reached that round on Polymarket yet).

    Each team's Yes-price is its own independent binary market, so the pair is
    renormalized to sum to 1 rather than trusted as-is.
    """
    for slug in [*_ROUND_CHAMPION_EVENT_SLUGS, f"{year}-nba-champion"]:
        tokens = _champion_event_team_tokens(slug)
        if team_a not in tokens or team_b not in tokens:
            continue
        sp_a, sp_b = start_prob(tokens[team_a], snap), start_prob(tokens[team_b], snap)
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

def odds_for_game(team_a: str, team_b: str, date: str) -> dict:
    """Market odds for a single game on a date (YYYY-MM-DD), cache-first.

    Returns {kind, date, probs:{A,B}, favored, source_slug, captured_ts} or {} when no
    market matches / no pre-start price exists yet. The pair is matched order-free by
    trying both slug orderings (nba-{a}-{b}-{date} and nba-{b}-{a}-{date}).
    """
    a, b = to_abbrev(team_a), to_abbrev(team_b)
    candidates = [f"nba-{a.lower()}-{b.lower()}-{date}", f"nba-{b.lower()}-{a.lower()}-{date}"]

    cache = load_odds_cache()
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
        probs, captured = {}, None
        for team, token in norm["tokens"].items():
            sp = start_prob(token, norm["start_ts"])
            if sp is None:
                return {}  # not started yet — leave uncached, retry later
            probs[team], captured = round(sp[0], 4), sp[1]
        favored = max(probs, key=probs.get)
        entry = {"kind": "game", "date": norm["date"], "probs": probs,
                 "favored": favored, "captured_ts": _iso(captured)}
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


def series_odds(team_a: str, team_b: str, year: int, games: list[dict] | None = None) -> dict:
    """Who-wins-series + total-games O/U odds for a matchup, cache-first.

    Returns {winner:{probs:{A,B}, favored, source_slug},
             total_games:{line, over_prob, under_prob, source_slug}, captured_ts} or {}
    when no market matches / the series can't be pinned to a start yet.
    """
    a, b = to_abbrev(team_a), to_abbrev(team_b)
    games = load_cache() if games is None else games
    snap = _series_snapshot_ts(a, b, games)
    if not snap:
        return {}

    markets = fetch_series_markets(year)
    win = next((m for m in markets["winner"] if {a, b} == set(m["teams"])), None)

    cache = load_odds_cache()
    if win:
        win_slug = win["slug"]
        if win_slug in cache:
            win_entry = cache[win_slug]
        else:
            probs, captured = {}, None
            for outcome, token in win["tokens"].items():
                sp = start_prob(token, snap)
                if sp is None:
                    return {}
                probs[to_abbrev(outcome)], captured = round(sp[0], 4), sp[1]
            win_entry = {"kind": "series-winner", "probs": probs,
                         "favored": max(probs, key=probs.get), "captured_ts": _iso(captured)}
            save_odds_entry(win_slug, win_entry)
    else:
        # Conference Finals / NBA Finals: no per-matchup slug, fall back to the
        # round's multi-team champion event (see _round_winner_probs).
        win_slug = next((s for s in [*_ROUND_CHAMPION_EVENT_SLUGS, f"{year}-nba-champion"]
                         if s in cache and {a, b} <= set(cache[s].get("probs", {}))), None)
        if win_slug:
            win_entry = cache[win_slug]
        else:
            fallback = _round_winner_probs(a, b, snap, year)
            if not fallback:
                return {}
            win_slug = fallback.pop("slug")
            win_entry = {"kind": "series-winner", **fallback}
            save_odds_entry(win_slug, win_entry)

    result = {
        "winner": {"probs": win_entry["probs"], "favored": win_entry["favored"],
                   "source_slug": win_slug},
        "captured_ts": win_entry["captured_ts"],
    }

    # Total-games O/U market — optional; include if present.
    tot = next((m for m in markets["total_games"] if {a, b} == set(m["teams"])), None)
    if tot:
        if tot["slug"] in cache:
            tot_entry = cache[tot["slug"]]
        else:
            over_token = next((t for o, t in tot["tokens"].items() if o.lower().startswith("over")), None)
            sp = start_prob(over_token, snap)
            tot_entry = None
            if sp is not None:
                tot_entry = {"kind": "series-total-games", "line": tot["line"],
                             "over_prob": round(sp[0], 4), "captured_ts": _iso(sp[1])}
                save_odds_entry(tot["slug"], tot_entry)
        if tot_entry:
            result["total_games"] = {"line": tot_entry["line"],
                                     "over_prob": tot_entry["over_prob"],
                                     "under_prob": round(1 - tot_entry["over_prob"], 4),
                                     "source_slug": tot["slug"]}
    return result


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


def odds_for_text(prediction_text: str, category: str, year: int | None = None) -> dict:
    """Grader entry point: market odds for the matchup named in a prediction's text.

    `series` -> series_odds; `game` -> pin the concrete game date via nba-games.json,
    then odds_for_game. The matchup is the two teams named in the text, or (when only
    one is named) the subject team plus an inferred opponent (see _resolve_matchup).
    A game is pinned when the teams meet once in the cache, or when the text names a
    "Game N" (playoff series meet repeatedly, but the claim names which game in
    chronological order). Returns {} when no market matches or it can't be pinned.
    """
    year = playoffs_year() if year is None else year
    games = load_cache()
    matchup = _resolve_matchup(prediction_text, games)
    if not matchup:
        return {}
    a, b = matchup

    if category == "series":
        return series_odds(a, b, year, games)

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
            return odds_for_game(a, b, meetings[n - 1]["date"])
        if len(meetings) != 1:
            return {}  # can't pin a single game -> leave empty rather than guess
        return odds_for_game(a, b, meetings[0]["date"])

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
    parser.add_argument("--year", type=int, default=playoffs_year(),
                        help="playoffs calendar year, e.g. 2026")
    parser.add_argument("--refresh", action="store_true",
                        help="OPTIONAL pre-warm the whole season's cache")
    parser.add_argument("--report", action="store_true",
                        help="audit which game/series predictions are missing odds and why")
    args = parser.parse_args()

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
        print(json.dumps(odds_for_game(a, b, args.date), indent=2))
        return
    if args.series:
        a, b = args.series.split()
        print(json.dumps(series_odds(a, b, args.year), indent=2))
        return
    if args.odds_for_text:
        if not args.category:
            parser.error("--odds-for-text requires --category")
        print(json.dumps(odds_for_text(args.odds_for_text, args.category, args.year), indent=2))
        return
    parser.error("nothing to do — pass --game, --series, --odds-for-text, --report, or --refresh")


if __name__ == "__main__":
    main()
