"""Read-only adapter from permanent historical rows to training log objects."""

from app.models import HistoricalGameLog, HistoricalGameOdds, PlayerGameLog

_FLAT_STAT_KEYS = (
    "pts",
    "reb",
    "ast",
    "stl",
    "blk",
    "tov",
    "fgm",
    "fga",
    "ftm",
    "fta",
    "fg3m",
    "fg3a",
    "minutes",
    "plus_minus",
)


def _adapt_historical_log(row: HistoricalGameLog) -> PlayerGameLog:
    stats = row.stats or {}
    home_away = (row.home_away or "").lower()
    separator = " vs. " if home_away == "home" else " @ "
    values = {key: stats.get(key, 0.0) for key in _FLAT_STAT_KEYS}
    log = PlayerGameLog(
        player_id=str(row.player_id),
        player_name=row.player_name,
        team_abbr=row.team_abbr,
        game_date=row.game_date,
        matchup=f"{row.team_abbr or ''}{separator}{row.opp_abbr or ''}",
        home_away=home_away,
        win_loss=row.win_loss,
        cache_expires=None,
        **values,
    )
    log._historical_game_id = row.game_id
    return log


def load_historical_training_logs(sport: str = "nba") -> list[PlayerGameLog]:
    """Return transient ``PlayerGameLog``-shaped rows from permanent history."""
    historical_rows = (
        HistoricalGameLog.query
        .filter_by(sport=sport)
        .order_by(HistoricalGameLog.player_id, HistoricalGameLog.game_date)
        .all()
    )
    return [_adapt_historical_log(row) for row in historical_rows]


def load_historical_game_total_lookup() -> dict[str, float]:
    """Return closing totals keyed by the shared ESPN game identifier."""
    return {
        str(row.espn_game_id): float(row.total)
        for row in HistoricalGameOdds.query.all()
        if row.espn_game_id
    }


def historical_training_store_has_rows(sport: str = "nba") -> bool:
    """Return whether the permanent store is the selected training source."""
    return (
        HistoricalGameLog.query
        .filter_by(sport=sport)
        .with_entities(HistoricalGameLog.id)
        .first()
        is not None
    )


def load_historical_replay_logs(
    player_id: str,
    game_date,
    sport: str = "nba",
) -> tuple[PlayerGameLog | None, list[PlayerGameLog]]:
    """Load one historical current row and its prior 82 games for replay."""
    current = (
        HistoricalGameLog.query
        .filter_by(sport=sport, player_id=str(player_id), game_date=game_date)
        .first()
    )
    if current is None:
        return None, []
    prior_rows = (
        HistoricalGameLog.query
        .filter(
            HistoricalGameLog.sport == sport,
            HistoricalGameLog.player_id == str(player_id),
            HistoricalGameLog.game_date < game_date,
        )
        .order_by(HistoricalGameLog.game_date.desc())
        .limit(82)
        .all()
    )
    return _adapt_historical_log(current), [
        _adapt_historical_log(row) for row in prior_rows
    ]
