"""Shared ESPN↔NBA mapping helpers.

HistoricalGameLog uses the ESPN id namespace (see app/cli/hoopr_import.py,
which imports these). Kept in services so both the CLI importer and the
game-day coordinator's history append share one source of truth.
"""

# ESPN abbreviations that differ from stats.nba.com convention.
ESPN_TO_NBA_ABBR = {
    'GS': 'GSW', 'NO': 'NOP', 'NY': 'NYK',
    'SA': 'SAS', 'UTAH': 'UTA', 'WSH': 'WAS',
}

NBA_TEAMS = frozenset({
    'ATL', 'BKN', 'BOS', 'CHA', 'CHI', 'CLE', 'DAL', 'DEN', 'DET', 'GSW',
    'HOU', 'IND', 'LAC', 'LAL', 'MEM', 'MIA', 'MIL', 'MIN', 'NOP', 'NYK',
    'OKC', 'ORL', 'PHI', 'PHX', 'POR', 'SAC', 'SAS', 'TOR', 'UTA', 'WAS',
})


def normalize_abbr(abbr: str) -> str:
    """Map an ESPN team abbreviation to NBA convention (passthrough if same)."""
    return ESPN_TO_NBA_ABBR.get(abbr, abbr)


def usage_pct(fga: float, fta: float, tov: float, minutes: float,
              team_minutes: float, team_fga: float, team_fta: float,
              team_tov: float) -> float:
    """Usage rate from box totals; 0.0 when the denominator degenerates."""
    denom = minutes * (team_fga + 0.44 * team_fta + team_tov)
    if denom <= 0:
        return 0.0
    return (fga + 0.44 * fta + tov) * (team_minutes / 5) / denom


def season_for_date(d) -> str:
    """NBA season string for a calendar date (seasons start in October)."""
    start_year = d.year if d.month >= 10 else d.year - 1
    return f"{start_year}-{str(start_year + 1)[-2:]}"
