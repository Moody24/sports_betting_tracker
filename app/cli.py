"""Flask CLI commands for manual job triggers."""

import logging
import time
from datetime import datetime, timezone

import click

from app import db
from app.models import JobLog, ModelMetadata, PlayerGameLog

logger = logging.getLogger(__name__)

BACKFILL_COMMIT_BATCH = 300
MAX_FETCH_FAILURES = 3


def _parse_player_ids(raw_player_ids: str) -> list[str]:
    if not raw_player_ids:
        return []
    return [pid.strip() for pid in raw_player_ids.split(',') if pid.strip()]


def _season_start_year(season: str) -> int:
    return int(str(season).split('-')[0])


def register_cli(app):
    """Register all CLI commands with the Flask app."""

    @app.cli.command('refresh-stats')
    def cli_refresh_stats():
        from app.services.scheduler import refresh_player_stats
        click.echo('Refreshing player stats...')
        refresh_player_stats()
        click.echo('Done.')

    @app.cli.command('refresh-defense')
    def cli_refresh_defense():
        from app.services.scheduler import refresh_defense_data
        click.echo('Refreshing defense data...')
        refresh_defense_data()
        click.echo('Done.')

    @app.cli.command('refresh-injuries')
    def cli_refresh_injuries():
        from app.services.scheduler import refresh_injury_reports
        click.echo('Refreshing injury reports...')
        refresh_injury_reports()
        click.echo('Done.')

    @app.cli.command('run-projections')
    def cli_run_projections():
        from app.services.scheduler import run_projections
        click.echo('Running projections...')
        run_projections()
        click.echo('Done.')

    @app.cli.command('grade-bets')
    def cli_grade_bets():
        from app.services.scheduler import resolve_and_grade
        click.echo('Grading bets...')
        resolve_and_grade()
        click.echo('Done.')

    @app.cli.command('retrain')
    def cli_retrain():
        from app.services.scheduler import retrain_models
        click.echo('Retraining models...')
        retrain_models()
        click.echo('Done.')

    @app.cli.command('generate-auto-picks')
    def cli_generate_auto_picks():
        from app.services.scheduler import generate_daily_auto_picks
        click.echo('Generating daily auto picks...')
        generate_daily_auto_picks()
        click.echo('Done.')

    @app.cli.command('backfill_player_logs')
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

        explicit_player_ids = _parse_player_ids(player_ids)
        if explicit_player_ids:
            all_candidates = nba_players.get_players()
            by_id = {str(p.get('id')): p for p in all_candidates}
            selected_players = [
                {'id': pid, 'full_name': by_id.get(pid, {}).get('full_name', f'Player {pid}')}
                for pid in explicit_player_ids
            ]
        elif players_scope == 'all':
            selected_players = nba_players.get_players()
        else:
            selected_players = nba_players.get_active_players()

        if max_players:
            selected_players = selected_players[:max_players]

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
                season_year = _season_start_year(season)
                if resume:
                    existing = (
                        PlayerGameLog.query
                        .filter_by(player_id=player_id)
                        .filter(PlayerGameLog.game_date >= datetime(season_year, 10, 1).date())
                        .filter(PlayerGameLog.game_date < datetime(season_year + 1, 10, 1).date())
                        .first()
                    )
                    if existing:
                        totals['players_skipped_resume'] += 1
                        click.echo(
                            f'Backfill: player {player_name}/{player_id}, season {season}, skipped (resume found data)'
                        )
                        continue

                logs = []
                last_exc = None
                for attempt, backoff in enumerate((0, 2, 5, 10), start=1):
                    try:
                        if backoff:
                            time.sleep(backoff)
                        logs = fetch_player_game_logs(
                            player_id,
                            season=season,
                            last_n=None,
                            raise_on_error=True,
                        )
                        break
                    except Exception as exc:
                        last_exc = exc
                        click.echo(
                            f'Backfill retry: player {player_name}/{player_id}, season {season}, '
                            f'attempt {attempt} failed: {exc}'
                        )

                if last_exc and not logs:
                    totals['fetch_failures'] += 1
                    detail = f'{player_name}/{player_id} {season}: {last_exc}'
                    failure_details.append(detail)
                    click.echo(f'Backfill error: {detail}')
                    if totals['fetch_failures'] >= MAX_FETCH_FAILURES:
                        click.echo('Backfill warning: maximum failures reached; continuing with remaining players.')
                    continue

                fetched_count = len(logs)
                totals['rows_fetched'] += fetched_count

                inserted = 0
                updated = 0
                if not dry_run and logs:
                    result = cache_player_logs(player_id, logs, ttl_days=3650, commit=False)
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

        if not dry_run:
            db.session.commit()

        summary = (
            'Backfill summary: '
            f"players_processed={totals['players_processed']}, "
            f"players_skipped_resume={totals['players_skipped_resume']}, "
            f"rows_fetched={totals['rows_fetched']}, "
            f"rows_inserted={totals['rows_inserted']}, "
            f"rows_updated={totals['rows_updated']}, "
            f"fetch_failures={totals['fetch_failures']}"
        )
        click.echo(summary)

        train_results = None
        if train_after and not dry_run:
            click.echo('Backfill complete; starting model retrain...')
            train_results = retrain_all_models()
            click.echo(f'Retrain results: {train_results}')

        job = db.session.get(JobLog, job.id)
        if job:
            job.finished_at = datetime.now(timezone.utc)
            job.status = 'success' if totals['fetch_failures'] == 0 else 'completed_with_errors'
            message = summary
            if failure_details:
                message = f"{summary}; sample_errors={'; '.join(failure_details[:5])}"
            if train_results is not None:
                message = f'{message}; train_after=true'
            job.message = message
            db.session.commit()

    @app.cli.command('model_status')
    def cli_model_status():
        """Show data/model/job diagnostics for backfill and training."""
        import os
        from sqlalchemy.exc import OperationalError

        ml_enabled = os.getenv('USE_ML_PROJECTIONS', 'false').lower() == 'true'

        try:
            total_logs = PlayerGameLog.query.count()
            unique_players = db.session.query(PlayerGameLog.player_id).distinct().count()
        except OperationalError as exc:
            click.echo(f'Database tables not ready: {exc}')
            return

        click.echo('=== PlayerGameLog ===')
        click.echo(f'Rows: {total_logs}')
        click.echo(f'Unique players: {unique_players}')

        click.echo('\n=== Projection engine mode ===')
        click.echo(f'USE_ML_PROJECTIONS={ml_enabled}')

        click.echo('\n=== ModelMetadata (latest active first) ===')
        models = (
            ModelMetadata.query
            .order_by(ModelMetadata.is_active.desc(), ModelMetadata.training_date.desc())
            .limit(20)
            .all()
        )
        if not models:
            click.echo('No model metadata records found.')
        for model in models:
            click.echo(
                f"- {model.model_name} v{model.version} | active={model.is_active} | "
                f"samples={model.training_samples or 0} | mae={model.val_mae} | "
                f"trained={model.training_date.isoformat() if model.training_date else 'n/a'}"
            )

        click.echo('\n=== Recent JobLog entries ===')
        jobs = JobLog.query.order_by(JobLog.started_at.desc()).limit(20).all()
        if not jobs:
            click.echo('No job log records found.')
        for job in jobs:
            click.echo(
                f"- {job.started_at.isoformat() if job.started_at else 'n/a'} | {job.job_name} | "
                f"status={job.status} | msg={job.message or ''}"
            )
