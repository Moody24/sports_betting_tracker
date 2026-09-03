"""Stats-related Flask CLI commands."""

import logging
import time
from datetime import datetime, timezone, timedelta

import click
from sqlalchemy import func

from app import db
from app.models import (
    InjuryReport,
    JobLog,
    PlayerGameLog,
    TeamDefenseSnapshot,
)
from app.cli import (
    _parse_player_ids,
    _season_start_year,
    APP_TIMEZONE,
    BACKFILL_COMMIT_BATCH,
    MAX_FETCH_FAILURES,
)

logger = logging.getLogger(__name__)

_BACKFILL_RETRY_BACKOFFS = (0, 2, 5, 10)


@click.command('refresh-stats')
def cli_refresh_stats():
    from app.services.scheduler import refresh_player_stats
    click.echo('Refreshing player stats...')
    refresh_player_stats()
    click.echo('Done.')


@click.command('refresh-defense')
def cli_refresh_defense():
    from app.services.scheduler import refresh_defense_data
    click.echo('Refreshing defense data...')
    refresh_defense_data()
    click.echo('Done.')


@click.command('refresh-injuries')
def cli_refresh_injuries():
    from app.services.scheduler import refresh_injury_reports
    click.echo('Refreshing injury reports...')
    refresh_injury_reports()
    click.echo('Done.')


def _select_backfill_players(nba_players, player_ids, players_scope, max_players):
    explicit_player_ids = _parse_player_ids(player_ids)
    if explicit_player_ids:
        all_candidates = nba_players.get_players()
        by_id = {str(player.get('id')): player for player in all_candidates}
        selected = [
            {
                'id': player_id,
                'full_name': by_id.get(player_id, {}).get(
                    'full_name',
                    f'Player {player_id}',
                ),
            }
            for player_id in explicit_player_ids
        ]
    elif players_scope == 'all':
        selected = nba_players.get_players()
    else:
        selected = nba_players.get_active_players()
    return selected[:max_players] if max_players else selected


def _has_backfill_season_data(player_id: str, season: str) -> bool:
    season_year = _season_start_year(season)
    return bool(
        PlayerGameLog.query
        .filter_by(player_id=player_id)
        .filter(PlayerGameLog.game_date >= datetime(season_year, 10, 1).date())
        .filter(PlayerGameLog.game_date < datetime(season_year + 1, 10, 1).date())
        .first()
    )


def _fetch_backfill_logs(fetch_logs, player_id, player_name, season):
    logs = []
    last_exc = None
    for attempt, backoff in enumerate(_BACKFILL_RETRY_BACKOFFS, start=1):
        try:
            if backoff:
                time.sleep(backoff)
            logs = fetch_logs(
                player_id,
                season=season,
                last_n=None,
                raise_on_error=True,
            )
            break
        except Exception as exc:
            last_exc = exc
            click.echo(
                f'Backfill retry: player {player_name}/{player_id}, '
                f'season {season}, attempt {attempt} failed: {exc}'
            )
    return logs, last_exc


def _record_backfill_failure(totals, failure_details, detail):
    totals['fetch_failures'] += 1
    failure_details.append(detail)
    click.echo(f'Backfill error: {detail}')
    if totals['fetch_failures'] >= MAX_FETCH_FAILURES:
        click.echo(
            'Backfill warning: maximum failures reached; '
            'continuing with remaining players.'
        )


def _process_backfill_season(
    *,
    player_id,
    player_name,
    season,
    resume,
    dry_run,
    sleep_seconds,
    pending_rows,
    totals,
    failure_details,
    fetch_logs,
    cache_logs,
):
    if resume and _has_backfill_season_data(player_id, season):
        totals['players_skipped_resume'] += 1
        click.echo(
            f'Backfill: player {player_name}/{player_id}, season {season}, '
            'skipped (resume found data)'
        )
        return pending_rows

    logs, last_exc = _fetch_backfill_logs(
        fetch_logs,
        player_id,
        player_name,
        season,
    )
    if last_exc and not logs:
        detail = f'{player_name}/{player_id} {season}: {last_exc}'
        _record_backfill_failure(totals, failure_details, detail)
        return pending_rows

    fetched_count = len(logs)
    totals['rows_fetched'] += fetched_count
    inserted = 0
    updated = 0
    if not dry_run and logs:
        result = cache_logs(player_id, logs, ttl_days=3650, commit=False)
        inserted = result['inserted']
        updated = result['updated']
        totals['rows_inserted'] += inserted
        totals['rows_updated'] += updated
        totals['rows_written'] += result['total']
        pending_rows += result['total']
        if pending_rows >= BACKFILL_COMMIT_BATCH:
            db.session.commit()
            pending_rows = 0

    click.echo(
        f'Backfill: player {player_name}/{player_id}, season {season}, '
        f'fetched {fetched_count} rows, inserted {inserted}, updated {updated}'
    )
    if sleep_seconds > 0:
        time.sleep(sleep_seconds)
    return pending_rows


def _run_player_log_backfill(
    selected_players,
    seasons,
    *,
    resume,
    dry_run,
    sleep_seconds,
    fetch_logs,
    cache_logs,
):
    totals = {
        'players_processed': 0,
        'players_skipped_resume': 0,
        'rows_fetched': 0,
        'rows_inserted': 0,
        'rows_updated': 0,
        'rows_written': 0,
        'fetch_failures': 0,
    }
    failure_details = []
    pending_rows = 0
    for player in selected_players:
        player_id = str(player.get('id'))
        player_name = player.get('full_name', player_id)
        totals['players_processed'] += 1
        for season in seasons:
            pending_rows = _process_backfill_season(
                player_id=player_id,
                player_name=player_name,
                season=season,
                resume=resume,
                dry_run=dry_run,
                sleep_seconds=sleep_seconds,
                pending_rows=pending_rows,
                totals=totals,
                failure_details=failure_details,
                fetch_logs=fetch_logs,
                cache_logs=cache_logs,
            )
    if not dry_run:
        db.session.commit()
    return totals, failure_details


def _backfill_summary(totals) -> str:
    return (
        'Backfill summary: '
        f"players_processed={totals['players_processed']}, "
        f"players_skipped_resume={totals['players_skipped_resume']}, "
        f"rows_fetched={totals['rows_fetched']}, "
        f"rows_inserted={totals['rows_inserted']}, "
        f"rows_updated={totals['rows_updated']}, "
        f"fetch_failures={totals['fetch_failures']}"
    )


def _finish_backfill_job(job_id, totals, summary, failure_details, trained):
    job = db.session.get(JobLog, job_id)
    if not job:
        return
    job.finished_at = datetime.now(timezone.utc)
    job.status = (
        'success' if totals['fetch_failures'] == 0 else 'completed_with_errors'
    )
    message = summary
    if failure_details:
        message = f"{summary}; sample_errors={'; '.join(failure_details[:5])}"
    if trained:
        message = f'{message}; train_after=true'
    job.message = message
    db.session.commit()


@click.command('backfill_player_logs')
@click.option('--seasons', multiple=True, required=True, help='Season values like 2024-25')
@click.option('--players', 'players_scope', type=click.Choice(['active', 'all']), default='active')
@click.option('--max-players', type=int, default=None)
@click.option('--sleep', 'sleep_seconds', type=float, default=0.6)
@click.option('--resume/--no-resume', default=True)
@click.option('--player-ids', default='')
@click.option('--train-after', is_flag=True)
@click.option('--dry-run', is_flag=True)
def cli_backfill_player_logs(
    seasons,
    players_scope,
    max_players,
    sleep_seconds,
    resume,
    player_ids,
    train_after,
    dry_run,
):
    """Backfill historical player game logs into PlayerGameLog."""
    from app.services.ml_model import retrain_all_models
    from app.services.stats_service import cache_player_logs, fetch_player_game_logs

    try:
        from nba_api.stats.static import players as nba_players
    except ImportError:
        click.echo('nba_api package not installed')
        return

    job = JobLog(
        job_name='backfill_player_logs',
        started_at=datetime.now(timezone.utc),
        status='running',
        message='Backfill started',
    )
    db.session.add(job)
    db.session.commit()
    selected_players = _select_backfill_players(
        nba_players,
        player_ids,
        players_scope,
        max_players,
    )
    totals, failure_details = _run_player_log_backfill(
        selected_players,
        seasons,
        resume=resume,
        dry_run=dry_run,
        sleep_seconds=sleep_seconds,
        fetch_logs=fetch_player_game_logs,
        cache_logs=cache_player_logs,
    )
    summary = _backfill_summary(totals)
    click.echo(summary)

    train_results = None
    if train_after and not dry_run:
        click.echo('Backfill complete; starting model retrain...')
        train_results = retrain_all_models()
        click.echo(f'Retrain results: {train_results}')

    _finish_backfill_job(
        job.id,
        totals,
        summary,
        failure_details,
        train_results is not None,
    )


@click.command('prune_player_logs')
def cli_prune_player_logs():
    """Delete expired and espn_* unresolvable rows from PlayerGameLog.

    Safe to run at any time. Use this to clean up rows created before the
    stat refresh was fixed to skip unresolvable players.
    """
    from app.services.stats_service import prune_expired_cache
    result = prune_expired_cache()
    click.echo(f"Pruned {result['expired']} expired rows.")
    click.echo(f"Pruned {result['unresolved']} unresolvable espn_* rows.")
    click.echo('Done.')


@click.command('backfill-game-snapshots')
@click.option('--start-date', required=True, help='Start date (YYYY-MM-DD)')
@click.option('--end-date', required=True, help='End date (YYYY-MM-DD)')
@click.option('--include-existing/--no-include-existing', default=False, show_default=True)
@click.option('--sleep', 'sleep_seconds', type=float, default=0.15, show_default=True)
def cli_backfill_game_snapshots(start_date, end_date, include_existing, sleep_seconds):
    """Backfill historical GameSnapshot rows from ESPN + bet-derived odds."""
    from app.services.nba_service import backfill_game_snapshots

    try:
        start_dt = datetime.strptime(start_date, '%Y-%m-%d').date()
        end_dt = datetime.strptime(end_date, '%Y-%m-%d').date()
    except ValueError:
        click.echo('Invalid date format. Use YYYY-MM-DD.')
        return

    click.echo(f'Backfilling game snapshots: {start_dt} -> {end_dt}')
    result = backfill_game_snapshots(
        start_date=start_dt,
        end_date=end_dt,
        include_existing=include_existing,
        sleep_seconds=sleep_seconds,
    )
    if result.get('error'):
        click.echo(f"Error: {result['error']}")
        return
    click.echo(
        f"Backfill result: scanned_days={result.get('scanned_days')} "
        f"scanned_games={result.get('scanned_games')} created={result.get('created')} "
        f"updated={result.get('updated')} ou_filled={result.get('ou_filled')} "
        f"moneyline_filled={result.get('moneyline_filled')}"
    )


@click.command('data_quality_report')
@click.option(
    '--stale-hours',
    type=int,
    default=36,
    show_default=True,
    help='Mark PlayerGameLog as stale when max game_date is older than this many hours.',
)
def cli_data_quality_report(stale_hours):
    """Print freshness/integrity checks for model input tables."""
    now_utc = datetime.now(timezone.utc)
    stale_cutoff_date = (now_utc - timedelta(hours=stale_hours)).date()
    report_today_et = datetime.now(APP_TIMEZONE).date()
    player_logs = _player_log_quality()
    context = _context_table_quality(report_today_et)
    stale_running_count = _stale_running_job_count(now_utc)
    issues = _data_quality_issues(
        player_logs, context, stale_running_count, stale_cutoff_date,
    )
    _print_data_quality_report(
        now_utc, report_today_et, stale_cutoff_date, player_logs, context,
        stale_running_count, issues,
    )


def _player_log_quality() -> dict:
    null_columns = ('pts', 'reb', 'ast', 'fg3m', 'minutes')
    nulls = {
        column: PlayerGameLog.query.filter(
            getattr(PlayerGameLog, column).is_(None),
        ).count()
        for column in null_columns
    }
    duplicates = (
        db.session.query(
            PlayerGameLog.player_id,
            PlayerGameLog.game_date,
            func.count(PlayerGameLog.id),
        )
        .group_by(PlayerGameLog.player_id, PlayerGameLog.game_date)
        .having(func.count(PlayerGameLog.id) > 1)
        .count()
    )
    return {
        'total': PlayerGameLog.query.count(),
        'max_date': db.session.query(func.max(PlayerGameLog.game_date)).scalar(),
        'min_date': db.session.query(func.min(PlayerGameLog.game_date)).scalar(),
        'unique_players': db.session.query(
            PlayerGameLog.player_id,
        ).distinct().count(),
        'nulls': nulls,
        'bad_minutes': PlayerGameLog.query.filter(
            (PlayerGameLog.minutes < 0) | (PlayerGameLog.minutes > 60),
        ).count(),
        'bad_points': PlayerGameLog.query.filter(
            (PlayerGameLog.pts < 0) | (PlayerGameLog.pts > 100),
        ).count(),
        'duplicates': duplicates,
    }


def _context_table_quality(report_date) -> dict:
    return {
        'injury_total': InjuryReport.query.count(),
        'injury_today': InjuryReport.query.filter(
            InjuryReport.date_reported == report_date,
        ).count(),
        'defense_total': TeamDefenseSnapshot.query.count(),
        'defense_today': TeamDefenseSnapshot.query.filter(
            TeamDefenseSnapshot.snapshot_date == report_date,
        ).count(),
    }


def _stale_running_job_count(now_utc: datetime) -> int:
    running_jobs = (
        JobLog.query
        .filter_by(status='running')
        .filter(JobLog.started_at.isnot(None))
        .all()
    )
    count = 0
    for job in running_jobs:
        started = job.started_at
        if started and started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        if started and (now_utc - started).total_seconds() > (180 * 60):
            count += 1
    return count


def _data_quality_issues(player_logs: dict, context: dict,
                         stale_running_count: int,
                         stale_cutoff_date) -> list[str]:
    issues = []
    if player_logs['total'] == 0:
        issues.append('PlayerGameLog has zero rows.')
    elif (not player_logs['max_date']
          or player_logs['max_date'] < stale_cutoff_date):
        issues.append(
            'PlayerGameLog is stale: '
            f"max game_date={player_logs['max_date']}, "
            f'cutoff={stale_cutoff_date}.'
        )
    if any(player_logs['nulls'].values()):
        issues.append('Null core stat values found in PlayerGameLog.')
    if player_logs['bad_minutes'] or player_logs['bad_points']:
        issues.append('Out-of-range values found in PlayerGameLog (minutes/points).')
    if player_logs['duplicates']:
        issues.append('Duplicate player_id+game_date rows found in PlayerGameLog.')
    if context['injury_today'] == 0:
        issues.append('No injuries recorded for today.')
    if context['defense_today'] == 0:
        issues.append('No defense snapshots recorded for today.')
    if stale_running_count:
        issues.append(f'{stale_running_count} running JobLog entries exceed 180 minutes.')
    return issues


def _print_data_quality_report(now_utc: datetime, report_today_et,
                               stale_cutoff_date, player_logs: dict,
                               context: dict, stale_running_count: int,
                               issues: list[str]) -> None:
    click.echo('=== Data Quality Report ===')
    click.echo(f'Generated UTC: {now_utc.isoformat()}')
    click.echo(f'Report day (ET): {report_today_et}')
    click.echo(f'Staleness cutoff (date): {stale_cutoff_date}')

    click.echo('\n=== PlayerGameLog ===')
    click.echo(f"Rows: {player_logs['total']}")
    click.echo(f"Unique players: {player_logs['unique_players']}")
    click.echo(
        f"Date range: {player_logs['min_date']} -> {player_logs['max_date']}"
    )
    nulls = player_logs['nulls']
    click.echo(
        'Nulls pts/reb/ast/fg3m/minutes: '
        f"{nulls['pts']}/{nulls['reb']}/{nulls['ast']}/"
        f"{nulls['fg3m']}/{nulls['minutes']}"
    )
    click.echo(
        'Out-of-range minutes/points: '
        f"{player_logs['bad_minutes']}/{player_logs['bad_points']}"
    )
    click.echo(f"Duplicate player+date keys: {player_logs['duplicates']}")

    click.echo('\n=== Context Tables ===')
    click.echo(
        'InjuryReport total/today: '
        f"{context['injury_total']}/{context['injury_today']}"
    )
    click.echo(
        'TeamDefenseSnapshot total/today: '
        f"{context['defense_total']}/{context['defense_today']}"
    )

    click.echo('\n=== Scheduler/Jobs ===')
    click.echo(f'Running jobs older than 180m: {stale_running_count}')

    click.echo('\n=== Verdict ===')
    if issues:
        click.echo('WARN')
        for issue in issues:
            click.echo(f'- {issue}')
    else:
        click.echo('PASS')


def register_stats_commands(app):
    app.cli.add_command(cli_refresh_stats)
    app.cli.add_command(cli_refresh_defense)
    app.cli.add_command(cli_refresh_injuries)
    app.cli.add_command(cli_backfill_player_logs)
    app.cli.add_command(cli_prune_player_logs)
    app.cli.add_command(cli_backfill_game_snapshots)
    app.cli.add_command(cli_data_quality_report)
