# NBA team abbreviations (canonical)

This table is the **single source of truth** for NBA team abbreviations across this repo.
The codes are exactly the 3-letter abbreviations `nba_api` emits — the same ones that
appear in `game-data/nba-games.json` and that `scripts/nba_games.py` (`to_abbrev`,
`build_facts`) produces. Whenever an LLM refers to a team by abbreviation — in a
`predictions.json` field or a `grade_note` — it **must** use the code from this table
verbatim, so abbreviations always match the game data and never drift (e.g. `PHX`, not
`PHO`; `NOP`, not `NO`).

Regenerate this table from `nba_api` if it ever needs updating:

```bash
uv run python -c "
from nba_api.stats.static import teams
for t in sorted(teams.get_teams(), key=lambda x: x['abbreviation']):
    print(f\"| {t['full_name']} | {t['abbreviation']} |\")
"
```

| Team | Abbrev |
| --- | --- |
| Atlanta Hawks | ATL |
| Brooklyn Nets | BKN |
| Boston Celtics | BOS |
| Charlotte Hornets | CHA |
| Chicago Bulls | CHI |
| Cleveland Cavaliers | CLE |
| Dallas Mavericks | DAL |
| Denver Nuggets | DEN |
| Detroit Pistons | DET |
| Golden State Warriors | GSW |
| Houston Rockets | HOU |
| Indiana Pacers | IND |
| Los Angeles Clippers | LAC |
| Los Angeles Lakers | LAL |
| Memphis Grizzlies | MEM |
| Miami Heat | MIA |
| Milwaukee Bucks | MIL |
| Minnesota Timberwolves | MIN |
| New Orleans Pelicans | NOP |
| New York Knicks | NYK |
| Oklahoma City Thunder | OKC |
| Orlando Magic | ORL |
| Philadelphia 76ers | PHI |
| Phoenix Suns | PHX |
| Portland Trail Blazers | POR |
| Sacramento Kings | SAC |
| San Antonio Spurs | SAS |
| Toronto Raptors | TOR |
| Utah Jazz | UTA |
| Washington Wizards | WAS |
