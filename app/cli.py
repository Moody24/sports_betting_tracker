"""Flask CLI commands for manual job triggers."""

import logging
import time
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import click
from sqlalchemy import func

from app import db
from app.models import (
    Bet,
    InjuryReport,
    JobLog,
    ModelMetadata,
    PickContext,
    PlayerGameLog,
    TeamDefenseSnapshot,
)

logger = logging.getLogger(__name__)
APP_TIMEZONE = ZoneInfo("America/New_York")

BACKFILL_COMMIT_BATCH = 300
MAX_FETCH_FAILURES = 3


def _parse_player_ids(raw_player_ids: str) -> list[str]:
    if not raw_player_ids:
        return []
    return [pid.strip() for pid in raw_player_ids.split(',') if pid.strip()]


def _season_start_year(season: str) -> int:
    return int(str(season).split('-')[0])


def _resolved_win_rate(days: int):
    """Return (resolved_bets, wins, rate) for resolved bets with pick context in the last N days.

    Returns None if there are no matching rows.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    resolved = (
        db.session.query(Bet, PickContext)
        .join(PickContext, Bet.id == PickContext.bet_id)
        .filter(Bet.outcome.in_(['win', 'lose']))
        .filter(Bet.match_date >= cutoff)
        .all()
    )
    if not resolved:
        return None
    wins = sum(1 for b, _ in resolved if b.outcome == 'win')
    return resolved, wins, wins / len(resolved)


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
    @click.option('--force', is_flag=True, default=False,
                  help='Skip age and row-count guardrails and retrain projection models immediately.')
    def cli_retrain(force):
        from app.services.scheduler import retrain_models
        from app.services.pick_quality_model import train_pick_quality_model
        click.echo('Retraining models...')
        if force:
            from app.services.ml_model import retrain_all_models
            click.echo('--force: bypassing guardrails for projection models.')
            results = retrain_all_models()
            click.echo(f'Projection retrain: {results}')
            pq_result = train_pick_quality_model()
            click.echo(f'Pick quality retrain: {pq_result}')
        else:
            retrain_models()
        click.echo('Done.')

    @app.cli.command('generate-auto-picks')
    def cli_generate_auto_picks():
        from app.services.scheduler import generate_daily_auto_picks
        click.echo('Generating daily auto picks...')
        generate_daily_auto_picks()
        click.echo('Done.')

    @app.cli.command('bootstrap-pick-quality')
    @click.option('--target', type=int, default=220, show_default=True, help='Target resolved examples for Model 2.')
    @click.option('--max-logs', type=int, default=10000, show_default=True, help='Max PlayerGameLog rows to scan.')
    @click.option('--train-after', is_flag=True, help='Train pick-quality model after backfill.')
    def cli_bootstrap_pick_quality(target, max_logs, train_after):
        from app.services.scheduler import bootstrap_pick_quality_examples
        from app.services.pick_quality_model import train_pick_quality_model

        click.echo('Bootstrapping hidden pick-quality training examples...')
        result = bootstrap_pick_quality_examples(target_resolved=target, max_logs=max_logs)
        click.echo(f'Bootstrap result: {result}')
        if train_after:
            click.echo('Training pick-quality model...')
            model_result = train_pick_quality_model()
            click.echo(f'Pick-quality train result: {model_result}')
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

        # 30-day rolling win-rate vs model training accuracy
        click.echo('\n=== 30-day Rolling Win Rate (pick quality) ===')
        win_rate_result = _resolved_win_rate(30)
        if win_rate_result:
            resolved_recent, wins_30d, rolling_win_rate = win_rate_result
            click.echo(f'Bets resolved (last 30d): {len(resolved_recent)}')
            click.echo(f'Rolling win rate: {rolling_win_rate:.3f} ({wins_30d}/{len(resolved_recent)})')
            pq_model = ModelMetadata.query.filter_by(
                model_name='pick_quality_nba', is_active=True,
            ).first()
            if pq_model and pq_model.val_accuracy:
                delta = rolling_win_rate - pq_model.val_accuracy
                click.echo(
                    f'vs model val_accuracy ({pq_model.val_accuracy:.3f}): '
                    f'delta={delta:+.3f}'
                )
                if abs(delta) > 0.05:
                    click.echo('WARN: >5% drift detected — consider retraining.')
            else:
                click.echo('No active pick_quality model metadata for comparison.')
        else:
            click.echo('No resolved bets with context in last 30 days.')

        click.echo('\n=== Recent JobLog entries ===')
        jobs = JobLog.query.order_by(JobLog.started_at.desc()).limit(20).all()
        if not jobs:
            click.echo('No job log records found.')
        for job in jobs:
            click.echo(
                f"- {job.started_at.isoformat() if job.started_at else 'n/a'} | {job.job_name} | "
                f"status={job.status} | msg={job.message or ''}"
            )

    @app.cli.command('data_quality_report')
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

        total_logs = PlayerGameLog.query.count()
        max_game_date = db.session.query(func.max(PlayerGameLog.game_date)).scalar()
        min_game_date = db.session.query(func.min(PlayerGameLog.game_date)).scalar()
        unique_players = db.session.query(PlayerGameLog.player_id).distinct().count()

        null_pts = PlayerGameLog.query.filter(PlayerGameLog.pts.is_(None)).count()
        null_reb = PlayerGameLog.query.filter(PlayerGameLog.reb.is_(None)).count()
        null_ast = PlayerGameLog.query.filter(PlayerGameLog.ast.is_(None)).count()
        null_fg3m = PlayerGameLog.query.filter(PlayerGameLog.fg3m.is_(None)).count()
        null_minutes = PlayerGameLog.query.filter(PlayerGameLog.minutes.is_(None)).count()

        bad_minutes = PlayerGameLog.query.filter(
            (PlayerGameLog.minutes < 0) | (PlayerGameLog.minutes > 60)
        ).count()
        bad_points = PlayerGameLog.query.filter(
            (PlayerGameLog.pts < 0) | (PlayerGameLog.pts > 100)
        ).count()

        duplicate_player_dates = (
            db.session.query(
                PlayerGameLog.player_id,
                PlayerGameLog.game_date,
                func.count(PlayerGameLog.id),
            )
            .group_by(PlayerGameLog.player_id, PlayerGameLog.game_date)
            .having(func.count(PlayerGameLog.id) > 1)
            .count()
        )

        injury_total = InjuryReport.query.count()
        injury_today = InjuryReport.query.filter(
            InjuryReport.date_reported == report_today_et
        ).count()
        defense_total = TeamDefenseSnapshot.query.count()
        defense_today = TeamDefenseSnapshot.query.filter(
            TeamDefenseSnapshot.snapshot_date == report_today_et
        ).count()

        stale_running_jobs = (
            JobLog.query
            .filter_by(status='running')
            .filter(JobLog.started_at.isnot(None))
            .all()
        )
        stale_running_count = 0
        for job in stale_running_jobs:
            started = job.started_at
            if started and started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            if started and (now_utc - started).total_seconds() > (180 * 60):
                stale_running_count += 1

        issues = []
        if total_logs == 0:
            issues.append('PlayerGameLog has zero rows.')
        elif not max_game_date or max_game_date < stale_cutoff_date:
            issues.append(
                f'PlayerGameLog is stale: max game_date={max_game_date}, cutoff={stale_cutoff_date}.'
            )
        if null_pts or null_reb or null_ast or null_fg3m or null_minutes:
            issues.append('Null core stat values found in PlayerGameLog.')
        if bad_minutes or bad_points:
            issues.append('Out-of-range values found in PlayerGameLog (minutes/points).')
        if duplicate_player_dates:
            issues.append('Duplicate player_id+game_date rows found in PlayerGameLog.')
        if injury_today == 0:
            issues.append('No injuries recorded for today.')
        if defense_today == 0:
            issues.append('No defense snapshots recorded for today.')
        if stale_running_count:
            issues.append(f'{stale_running_count} running JobLog entries exceed 180 minutes.')

        click.echo('=== Data Quality Report ===')
        click.echo(f'Generated UTC: {now_utc.isoformat()}')
        click.echo(f'Report day (ET): {report_today_et}')
        click.echo(f'Staleness cutoff (date): {stale_cutoff_date}')

        click.echo('\n=== PlayerGameLog ===')
        click.echo(f'Rows: {total_logs}')
        click.echo(f'Unique players: {unique_players}')
        click.echo(f'Date range: {min_game_date} -> {max_game_date}')
        click.echo(
            'Nulls pts/reb/ast/fg3m/minutes: '
            f'{null_pts}/{null_reb}/{null_ast}/{null_fg3m}/{null_minutes}'
        )
        click.echo(f'Out-of-range minutes/points: {bad_minutes}/{bad_points}')
        click.echo(f'Duplicate player+date keys: {duplicate_player_dates}')

        click.echo('\n=== Context Tables ===')
        click.echo(f'InjuryReport total/today: {injury_total}/{injury_today}')
        click.echo(f'TeamDefenseSnapshot total/today: {defense_total}/{defense_today}')

        click.echo('\n=== Scheduler/Jobs ===')
        click.echo(f'Running jobs older than 180m: {stale_running_count}')

        click.echo('\n=== Verdict ===')
        if issues:
            click.echo('WARN')
            for issue in issues:
                click.echo(f'- {issue}')
        else:
            click.echo('PASS')

    @app.cli.command('prune_player_logs')
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

    @app.cli.command('drift_report')
    @click.option('--days', type=int, default=30, show_default=True, help='Rolling window in days.')
    def cli_drift_report(days):
        """Report rolling win-rate vs model training accuracy."""
        click.echo(f'=== Drift Report (last {days} days) ===')
        win_rate_result = _resolved_win_rate(days)
        if not win_rate_result:
            click.echo(f'No resolved bets with pick context in last {days} days.')
            return

        resolved, wins, rolling_rate = win_rate_result
        click.echo(f'Resolved bets: {len(resolved)}')
        click.echo(f'Rolling win rate: {rolling_rate:.3f} ({wins}/{len(resolved)})')

        pq_model = ModelMetadata.query.filter_by(
            model_name='pick_quality_nba', is_active=True,
        ).first()
        if pq_model and pq_model.val_accuracy:
            delta = rolling_rate - pq_model.val_accuracy
            click.echo(f'Model val_accuracy: {pq_model.val_accuracy:.3f}')
            click.echo(f'Delta (rolling - val): {delta:+.3f}')
            if abs(delta) > 0.05:
                click.echo('VERDICT: DRIFT DETECTED — rolling accuracy has drifted >5% from training.')
            else:
                click.echo('VERDICT: OK — rolling accuracy within 5% of training accuracy.')
        else:
            click.echo('No active pick_quality_nba model found for comparison.')
            click.echo(f'VERDICT: Rolling win rate only: {rolling_rate:.3f}')

    @app.cli.command('model_calibration_report')
    @click.option(
        '--limit',
        type=int,
        default=500,
        show_default=True,
        help='Max most-recent resolved picks with context to evaluate.',
    )
    @click.option(
        '--bins',
        type=int,
        default=5,
        show_default=True,
        help='Number of probability bins for calibration table (2-10).',
    )
    @click.option(
        '--user-id',
        type=int,
        default=None,
        help='Optional user ID for per-user model calibration.',
    )
    def cli_model_calibration_report(limit, bins, user_id):
        """Print pick-quality model calibration and confidence diagnostics."""
        from app.services.pick_quality_model import get_calibration_report

        report = get_calibration_report(limit=limit, bins=bins, user_id=user_id)

        click.echo('=== Model Calibration Report (Model 2) ===')
        if user_id is not None:
            click.echo(f'User ID: {user_id}')

        if report.get('error'):
            click.echo(f"Error: {report['error']}")
            if 'total_rows' in report:
                click.echo(f"Resolved rows scanned: {report.get('total_rows', 0)}")
                click.echo(f"No-model rows: {report.get('no_model_count', 0)}")
            return

        click.echo(f"Model version: {report.get('model_version') or 'unknown'}")
        click.echo(
            "Rows scanned/evaluated/no-model: "
            f"{report.get('total_rows', 0)}/{report.get('evaluated', 0)}/{report.get('no_model_count', 0)}"
        )
        click.echo(f"Wins/Losses: {report.get('wins', 0)}/{report.get('losses', 0)}")
        click.echo(
            "Avg predicted vs actual win rate: "
            f"{report.get('avg_pred', 0):.3f} vs {report.get('win_rate', 0):.3f}"
        )
        click.echo(
            f"Overconfidence gap (pred - actual): {report.get('overconfidence_gap', 0):.3f}"
        )
        click.echo(f"Brier score (lower is better): {report.get('brier', 0):.4f}")
        click.echo(f"Log loss (lower is better): {report.get('logloss', 0):.4f}")

        rec = report.get('recommendation_counts', {})
        click.echo(
            "Recommendation mix: "
            f"take_it={rec.get('take_it', 0)}, "
            f"caution={rec.get('caution', 0)}, "
            f"skip={rec.get('skip', 0)}, "
            f"no_model={rec.get('no_model', 0)}"
        )

        click.echo('\n=== Calibration Bins ===')
        for row in report.get('bins', []):
            if row.get('count', 0) == 0:
                click.echo(f"- {row.get('range')}: count=0")
                continue
            click.echo(
                f"- {row.get('range')}: count={row.get('count')}, "
                f"pred={row.get('avg_pred'):.3f}, actual={row.get('win_rate'):.3f}, "
                f"gap={row.get('gap'):.3f}"
            )

        click.echo('\n=== Verdict ===')
        gap = abs(float(report.get('overconfidence_gap', 0) or 0))
        if gap <= 0.03:
            click.echo('GOOD: model appears reasonably calibrated on this sample.')
        elif gap <= 0.07:
            click.echo('WATCH: mild confidence skew detected; monitor next retrains.')
        else:
            click.echo('WARN: significant over/under-confidence; recalibration advised.')
