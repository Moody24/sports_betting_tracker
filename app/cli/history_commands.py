"""CLI commands for the permanent HistoricalGameLog store."""

import logging
import time
from datetime import date, datetime, timezone

import click

from app import db
from app.models import HistoricalGameLog, JobLog
from app.services.ml_feature_builder import extract_opp_abbr
from app.utils.time_helpers import ET

logger = logging.getLogger(__name__)

# LeagueGameLog column → stats-payload key (all coerced to float)
_NBA_STAT_COLUMNS = {
    'PTS': 'pts', 'REB': 'reb', 'AST': 'ast', 'STL': 'stl', 'BLK': 'blk',
    'TOV': 'tov', 'FGM': 'fgm', 'FGA': 'fga', 'FG3M': 'fg3m', 'FG3A': 'fg3a',
    'FTM': 'ftm', 'FTA': 'fta', 'MIN': 'minutes', 'PLUS_MINUS': 'plus_minus',
}


def _recent_seasons(n: int, today: date | None = None) -> list[str]:
    """Most recent ``n`` NBA season strings, newest first.

    NBA seasons start in October: before October, the 'current' season is
    the one that started last calendar year.
    """
    today = today or datetime.now(ET).date()
    start_year = today.year if today.month >= 10 else today.year - 1
    return [
        f"{y}-{str(y + 1)[-2:]}"
        for y in range(start_year, start_year - n, -1)
    ]


def _fetch_league_log_df(season: str, season_type: str):
    """One nba_api call for a full season of player game logs."""
    from nba_api.stats.endpoints import leaguegamelog
    log = leaguegamelog.LeagueGameLog(
        season=season,
        season_type_all_star=season_type,
        player_or_team_abbreviation='P',
        timeout=60,
    )
    return log.get_data_frames()[0]


def _rows_from_league_log(df, season: str) -> list[dict]:
    """Map a LeagueGameLog dataframe to HistoricalGameLog constructor kwargs."""
    rows = []
    for rec in df.to_dict('records'):
        matchup = str(rec.get('MATCHUP') or '')
        stats = {}
        for col, key in _NBA_STAT_COLUMNS.items():
            try:
                stats[key] = float(rec.get(col) or 0.0)
            except (TypeError, ValueError):
                stats[key] = 0.0
        rows.append(dict(
            sport='nba',
            player_id=str(rec.get('PLAYER_ID', '')),
            player_name=str(rec.get('PLAYER_NAME', '')),
            team_abbr=str(rec.get('TEAM_ABBREVIATION') or '') or None,
            opp_abbr=extract_opp_abbr(matchup) or None,
            game_id=str(rec.get('GAME_ID', '')),
            game_date=datetime.strptime(
                str(rec.get('GAME_DATE', '')), '%Y-%m-%d').date(),
            season=season,
            home_away='HOME' if ' vs. ' in matchup else 'AWAY',
            win_loss=str(rec.get('WL') or '') or None,
            starter=None,          # filled by `flask enrich-logs`
            stats=stats,
        ))
    return rows


@click.command('backfill-logs')
@click.option('--sport', default='nba', show_default=True)
@click.option('--seasons', default=3, show_default=True, type=int)
@click.option('--season-type', default='Regular Season', show_default=True)
@click.option('--sleep', 'sleep_seconds', default=1.5, show_default=True,
              type=float, help='Pause between season fetches (rate limit).')
def cli_backfill_logs(sport, seasons, season_type, sleep_seconds):
    """Backfill HistoricalGameLog from season-wide league game logs."""
    if sport != 'nba':
        raise click.BadParameter(
            f"sport '{sport}' not supported yet (nba only; mlb/nfl are "
            "Phase 3/4)")

    job = JobLog(job_name='backfill-logs',
                 started_at=datetime.now(timezone.utc), status='running')
    db.session.add(job)
    db.session.commit()

    inserted = skipped = 0
    errors: list[str] = []

    for season in _recent_seasons(seasons):
        try:
            df = _fetch_league_log_df(season, season_type)
        except Exception as exc:  # nba_api raises assorted exception types
            errors.append(f"{season}: {exc}")
            logger.error("backfill-logs: season %s fetch failed: %s",
                         season, exc)
            continue

        existing = {
            (pid, gid) for pid, gid in db.session.query(
                HistoricalGameLog.player_id, HistoricalGameLog.game_id,
            ).filter_by(sport=sport, season=season)
        }
        batch = []
        for kwargs in _rows_from_league_log(df, season):
            if (kwargs['player_id'], kwargs['game_id']) in existing:
                skipped += 1
                continue
            batch.append(HistoricalGameLog(**kwargs))
        db.session.add_all(batch)
        db.session.commit()
        inserted += len(batch)
        click.echo(f"{season}: +{len(batch)} rows ({skipped} already present)")
        if sleep_seconds:
            time.sleep(sleep_seconds)

    job.finished_at = datetime.now(timezone.utc)
    job.status = 'failed' if errors else 'success'
    job.message = (
        f"inserted={inserted} skipped={skipped}"
        + (f" errors={'; '.join(errors)}" if errors else "")
    )
    db.session.commit()
    click.echo(f"Done: {job.message}")


def register_history_commands(app):
    app.cli.add_command(cli_backfill_logs)
