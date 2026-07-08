"""Per-sport stat catalogs for HistoricalGameLog payloads.

The ``stats`` JSON column on ``HistoricalGameLog`` holds whatever keys the
sport's catalog defines.  Feature builders and the scenario engine iterate
``stat_keys`` instead of hard-coding NBA columns, which is what makes them
sport-agnostic.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class SportStatConfig:
    sport_key: str
    stat_keys: tuple[str, ...]


SPORT_STAT_CONFIG: dict[str, SportStatConfig] = {
    'nba': SportStatConfig(
        sport_key='nba',
        stat_keys=(
            'pts', 'reb', 'ast', 'stl', 'blk', 'tov',
            'fgm', 'fga', 'fg3m', 'fg3a', 'ftm', 'fta',
            'minutes', 'plus_minus', 'usage_pct',
        ),
    ),
    'mlb': SportStatConfig(
        sport_key='mlb',
        stat_keys=(
            'hits', 'total_bases', 'home_runs', 'rbis', 'runs',
            'stolen_bases', 'walks', 'strikeouts_batter',
            'strikeouts_pitcher', 'outs_recorded', 'earned_runs',
            'hits_allowed', 'walks_allowed',
        ),
    ),
    'nfl': SportStatConfig(
        sport_key='nfl',
        stat_keys=(
            'pass_yds', 'pass_tds', 'pass_attempts', 'completions',
            'interceptions', 'rush_yds', 'rush_attempts', 'rush_tds',
            'receptions', 'rec_yds', 'rec_tds', 'targets',
        ),
    ),
}


def get_stat_config(sport_key: str) -> SportStatConfig:
    """Return the stat catalog for a sport.  Raises KeyError if unknown."""
    return SPORT_STAT_CONFIG[sport_key]
