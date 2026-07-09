"""CLI commands for the permanent HistoricalGameLog store."""

import logging
import math
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


def _safe_float(value, default: float = 0.0) -> float:
    """Coerce ``value`` to float, falling back to ``default``.

    Handles the pandas/nba_api sentinels for missing data: ``None`` and
    NaN (``float`` truthy, so a plain ``or`` fallback lets it through).
    """
    if value is None:
        return default
    try:
        as_float = float(value)
    except (TypeError, ValueError):
        return default
    return default if math.isnan(as_float) else as_float


def _safe_str(value) -> str:
    """Coerce ``value`` to str, treating None/NaN as ''.

    ``str(float('nan'))`` produces the literal string ``'nan'``, which
    would otherwise leak into nullable text columns via ``or None``.
    """
    if value is None:
        return ''
    if isinstance(value, float) and math.isnan(value):
        return ''
    return str(value)


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
        matchup = _safe_str(rec.get('MATCHUP'))
        stats = {}
        for col, key in _NBA_STAT_COLUMNS.items():
            stats[key] = _safe_float(rec.get(col))
        rows.append(dict(
            sport='nba',
            player_id=_safe_str(rec.get('PLAYER_ID')),
            player_name=_safe_str(rec.get('PLAYER_NAME')),
            team_abbr=_safe_str(rec.get('TEAM_ABBREVIATION')) or None,
            opp_abbr=extract_opp_abbr(matchup) or None,
            game_id=_safe_str(rec.get('GAME_ID')),
            game_date=datetime.strptime(
                _safe_str(rec.get('GAME_DATE')), '%Y-%m-%d').date(),
            season=season,
            home_away='HOME' if ' vs. ' in matchup else 'AWAY',
            win_loss=_safe_str(rec.get('WL')) or None,
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

        try:
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
            click.echo(
                f"{season}: +{len(batch)} rows ({skipped} already present)")
            if sleep_seconds:
                time.sleep(sleep_seconds)
        except Exception as exc:  # malformed rows, DB errors, etc.
            db.session.rollback()
            errors.append(f"{season}: {exc}")
            logger.error("backfill-logs: season %s processing failed: %s",
                         season, exc)
            continue

    job.finished_at = datetime.now(timezone.utc)
    job.status = 'failed' if errors else 'success'
    job.message = (
        f"inserted={inserted} skipped={skipped}"
        + (f" errors={'; '.join(errors)}" if errors else "")
    )
    db.session.commit()
    click.echo(f"Done: {job.message}")


def _fetch_advanced_boxscore_df(game_id: str):
    """One nba_api call: advanced box score (USG_PCT, START_POSITION)."""
    from nba_api.stats.endpoints import boxscoreadvancedv2
    box = boxscoreadvancedv2.BoxScoreAdvancedV2(game_id=game_id, timeout=60)
    return box.get_data_frames()[0]   # player-level frame


@click.command('enrich-logs')
@click.option('--sport', default='nba', show_default=True)
@click.option('--limit', default=200, show_default=True, type=int,
              help='Max games to enrich this run (chunkable).')
@click.option('--sleep', 'sleep_seconds', default=0.8, show_default=True,
              type=float)
def cli_enrich_logs(sport, limit, sleep_seconds):
    """Merge advanced box-score data (usage, starter) into HistoricalGameLog.

    Rows with ``starter IS NULL`` are un-enriched; one API call per game.
    """
    if sport != 'nba':
        raise click.BadParameter(f"sport '{sport}' not supported yet")

    pending_games = [
        gid for (gid,) in db.session.query(HistoricalGameLog.game_id)
        .filter_by(sport=sport)
        .filter(HistoricalGameLog.starter.is_(None))
        .distinct().order_by(HistoricalGameLog.game_id)
        .limit(limit)
    ]
    enriched = failed = 0
    for gid in pending_games:
        try:
            df = _fetch_advanced_boxscore_df(gid)
        except Exception as exc:
            failed += 1
            logger.warning("enrich-logs: game %s fetch failed: %s", gid, exc)
            continue
        by_player = {
            str(rec.get('PLAYER_ID', '')): rec for rec in df.to_dict('records')
        }
        rows = HistoricalGameLog.query.filter_by(
            sport=sport, game_id=gid).all()
        for row in rows:
            rec = by_player.get(row.player_id)
            if rec is None:
                continue
            row.starter = bool(_safe_str(rec.get('START_POSITION')).strip())
            new_stats = dict(row.stats or {})
            new_stats['usage_pct'] = _safe_float(rec.get('USG_PCT'))
            row.stats = new_stats   # reassign — JSON columns don't track mutation
        db.session.commit()
        enriched += 1
        if sleep_seconds:
            time.sleep(sleep_seconds)

    click.echo(f"Enriched {enriched} games ({failed} failed, "
               f"{len(pending_games)} attempted)")


def register_history_commands(app):
    app.cli.add_command(cli_backfill_logs)
    app.cli.add_command(cli_enrich_logs)
