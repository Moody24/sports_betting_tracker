"""Flask CLI commands for manual job triggers."""

import logging
import time
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import click
from sqlalchemy import func
from sqlalchemy.exc import OperationalError

from app import db
from app.models import (
    Bet,
    BetPostmortem,
    InjuryReport,
    JobLog,
    ModelMetadata,
    OddsSnapshot,
    PickContext,
    PlayerGameLog,
    TeamDefenseSnapshot,
)

logger = logging.getLogger(__name__)
APP_TIMEZONE = ZoneInfo("America/New_York")

BACKFILL_COMMIT_BATCH = 300
MAX_FETCH_FAILURES = 3


def _as_utc(dt: datetime | None) -> datetime | None:
    """Normalize DB datetimes to UTC-aware for safe arithmetic."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_player_ids(raw_player_ids: str) -> list[str]:
    if not raw_player_ids:
        return []
    return [pid.strip() for pid in raw_player_ids.split(',') if pid.strip()]


def _season_start_year(season: str) -> int:
    return int(str(season).split('-')[0])


def _resolved_win_rate(days: int):
    """Return segmented win rates for resolved bets with pick context in the last N days.

    Returns a dict with keys: manual, auto, real (manual+auto), bootstrap, all.
    Each value is (count, wins, rate) or None when that segment has no rows.
    Returns None when there are no matching rows at all.

    Segments:
      manual    — source='manual' bets placed by a real user
      auto      — source='auto_generated' real system picks (not bootstrap synthetic data)
      real      — manual + auto combined; used for drift comparison vs val_accuracy
      bootstrap — source='auto_generated' + notes starting with 'AUTO_BOOTSTRAP_HIDDEN';
                  synthetic training data — excluded from drift comparison to avoid
                  comparing the model against its own training set
      all       — every resolved bet with a PickContext
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (
        db.session.query(Bet, PickContext)
        .join(PickContext, Bet.id == PickContext.bet_id)
        .filter(Bet.outcome.in_(['win', 'lose']))
        .filter(Bet.match_date >= cutoff)
        .all()
    )
    if not rows:
        return None

    def _rate(subset):
        if not subset:
            return None
        wins = sum(1 for b, _ in subset if b.outcome == 'win')
        return len(subset), wins, wins / len(subset)

    manual = [(b, pc) for b, pc in rows if b.source == 'manual']
    auto = [
        (b, pc) for b, pc in rows
        if b.source == 'auto_generated'
        and not (b.notes or '').startswith('AUTO_BOOTSTRAP_HIDDEN')
    ]
    bootstrap = [
        (b, pc) for b, pc in rows
        if b.source == 'auto_generated'
        and (b.notes or '').startswith('AUTO_BOOTSTRAP_HIDDEN')
    ]
    return {
        'manual': _rate(manual),
        'auto': _rate(auto),
        'real': _rate(manual + auto),
        'bootstrap': _rate(bootstrap),
        'all': _rate(rows),
    }


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
        from app.services.market_recommender import train_market_models
        click.echo('Retraining models...')
        if force:
            from app.services.ml_model import retrain_all_models
            click.echo('--force: bypassing guardrails for projection models.')
            results = retrain_all_models()
            click.echo(f'Projection retrain: {results}')
            pq_result = train_pick_quality_model()
            click.echo(f'Pick quality retrain: {pq_result}')
            market_result = train_market_models()
            click.echo(f'Market models retrain: {market_result}')
        else:
            retrain_models()
        click.echo('Done.')

    @app.cli.command('train-market-models')
    @click.option('--min-samples', type=int, default=60, show_default=True)
    def cli_train_market_models(min_samples):
        from app.services.market_recommender import train_market_models
        click.echo('Training market models (moneyline + total O/U)...')
        result = train_market_models(min_samples=min_samples)
        click.echo(f'Market model train result: {result}')

    @app.cli.command('market-model-report')
    @click.option('--days', type=int, default=60, show_default=True, help='Rolling evaluation window in days.')
    @click.option('--bins', type=int, default=5, show_default=True, help='Calibration bins (2-10).')
    @click.option(
        '--drift-threshold',
        type=float,
        default=0.05,
        show_default=True,
        help='Absolute accuracy delta threshold to flag drift.',
    )
    def cli_market_model_report(days, bins, drift_threshold):
        """Evaluate market models (moneyline + total O/U) on recent finals."""
        from app.services.market_recommender import evaluate_market_models

        report = evaluate_market_models(days=days, bins=bins)
        click.echo(f'=== Market Model Report (last {days} days) ===')
        if report.get('error'):
            click.echo(f"Error: {report['error']}")
            click.echo(f"Rows scanned: {report.get('rows_scanned', 0)}")
            return

        click.echo(f"Rows scanned: {report.get('rows_scanned', 0)}")
        policy = report.get('policy_used') or {}
        if policy:
            click.echo(f"Policy used: {policy}")
        markets = report.get('markets', {})
        if not markets:
            click.echo('No market metrics available.')
            return

        drift_flags = []
        for market_name in ('moneyline', 'total_ou'):
            m = markets.get(market_name)
            if not m:
                continue
            click.echo(f'\n--- {market_name} ---')
            if m.get('error'):
                click.echo(f"Error: {m['error']}")
                continue
            click.echo(
                f"Rows={m.get('rows', 0)} | "
                f"Accuracy={m.get('accuracy')} | "
                f"Brier={m.get('brier')} | "
                f"LogLoss={m.get('logloss')}"
            )
            click.echo(
                f"AvgPred={m.get('avg_pred')} vs Actual={m.get('actual_rate')} | "
                f"Gap={m.get('overconfidence_gap')}"
            )
            click.echo(
                f"Recommended bets={m.get('recommended_bets')} "
                f"({m.get('recommended_bet_rate')}) | "
                f"Recommended hit rate={m.get('recommended_hit_rate')} | "
                f"ROI/bet={m.get('roi_per_bet')} | "
                f"CLV-proxy={m.get('closing_edge_proxy')}"
            )
            click.echo(
                f"Train val_acc={m.get('train_val_accuracy')} | "
                f"Delta={m.get('accuracy_delta')} | "
                f"Train logloss={m.get('train_val_logloss')} | "
                f"Logloss delta={m.get('logloss_delta')}"
            )

            acc_delta = m.get('accuracy_delta')
            if acc_delta is not None and abs(float(acc_delta)) > drift_threshold:
                drift_flags.append(
                    f"{market_name}: accuracy delta {acc_delta:+.3f} exceeds {drift_threshold:.3f}",
                )

            click.echo('Calibration bins:')
            for b in m.get('bins', []):
                if b.get('count', 0) == 0:
                    click.echo(f"  - {b.get('range')}: count=0")
                    continue
                click.echo(
                    f"  - {b.get('range')}: count={b.get('count')}, "
                    f"pred={b.get('avg_pred')}, actual={b.get('win_rate')}, gap={b.get('gap')}"
                )

        click.echo('\n=== Verdict ===')
        if drift_flags:
            click.echo('WARN: market drift indicators detected.')
            for line in drift_flags:
                click.echo(f'- {line}')
        else:
            click.echo('OK: no market drift beyond threshold.')

    @app.cli.command('market-threshold-tune')
    @click.option('--days', type=int, default=180, show_default=True, help='History window in days.')
    @click.option('--bins', type=int, default=5, show_default=True, help='Calibration bins (2-10).')
    @click.option('--min-bets', type=int, default=40, show_default=True, help='Minimum recommended bets per market.')
    @click.option('--apply/--no-apply', default=True, show_default=True, help='Persist tuned thresholds to active model metadata.')
    def cli_market_threshold_tune(days, bins, min_bets, apply):
        """Tune market recommendation thresholds for ROI + CLV proxy + calibration."""
        from app.services.market_recommender import tune_market_thresholds

        click.echo(f'=== Market Threshold Tune (last {days} days) ===')
        result = tune_market_thresholds(days=days, bins=bins, min_bets=min_bets, apply=apply)
        if result.get('error'):
            click.echo(f"Error: {result['error']}")
            click.echo(f"Rows scanned: {result.get('rows_scanned', 0)}")
            return

        click.echo(f"Selected policy: {result.get('policy')}")
        selected = result.get('selected', {})
        for market in ('moneyline', 'total_ou'):
            s = selected.get(market, {})
            click.echo(f"\n--- {market} ---")
            click.echo(f"Thresholds: {s.get('selected')}")
            click.echo(f"Objective score: {s.get('score')}")
            metrics = s.get('metrics') or {}
            if metrics:
                click.echo(
                    f"Bets={metrics.get('recommended_bets')} | "
                    f"ROI/bet={metrics.get('roi_per_bet')} | "
                    f"CLV-proxy={metrics.get('closing_edge_proxy')} | "
                    f"Cal gap={metrics.get('overconfidence_gap')}"
                )
            else:
                click.echo('No valid candidate met min-bets gate; kept previous/default thresholds.')

        click.echo('\n=== Apply ===')
        if result.get('applied'):
            click.echo(f"Applied: {result.get('apply_result')}")
        else:
            click.echo('No changes persisted (--no-apply).')

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
        """Report rolling win-rate vs model training accuracy, segmented by bet source."""
        click.echo(f'=== Drift Report (last {days} days) ===')
        win_rate_result = _resolved_win_rate(days)
        if not win_rate_result:
            click.echo(f'No resolved bets with pick context in last {days} days.')
            return

        def _fmt(label, seg):
            if seg is None:
                click.echo(f'  {label}: no data')
            else:
                count, wins, rate = seg
                click.echo(f'  {label}: {rate:.1%} ({wins}/{count})')

        _fmt('Manual bets', win_rate_result['manual'])
        _fmt('Auto picks (real)', win_rate_result['auto'])
        real = win_rate_result['real']
        if real:
            count, wins, rolling_rate = real
            click.echo(f'Rolling win rate: {rolling_rate:.3f} ({wins}/{count})')
        _fmt('Bootstrap synthetic', win_rate_result['bootstrap'])

        if not real:
            click.echo('No real (non-bootstrap) bets for drift comparison.')
            return

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

    @click.command('pollution_report')
    @click.option('--fix', is_flag=True, default=False,
                  help='Delete polluted bootstrap rows and deactivate stale models.')
    @click.option('--retrain-after', is_flag=True, default=False,
                  help='Retrain Model 2 after cleaning polluted rows.')
    def cli_pollution_report(fix, retrain_after):
        """Diagnose and optionally clean data pollution that causes model drift.

        Checks for:
        - Bootstrap synthetic bets with zeroed-out matchup context
        - Auto-pick bets with polluted (empty opponent/team) PickContext
        - Stale or orphaned model metadata entries
        """
        import json as _json

        with app.app_context():
            click.echo('=== Data Pollution Report ===')

            # 1. Count bootstrap bets
            try:
                bootstrap_total = (
                    Bet.query
                    .filter(Bet.notes.like('AUTO_BOOTSTRAP_HIDDEN%'))
                    .count()
                )
            except OperationalError:
                click.echo('Database tables are not initialized; run migrations first.')
                return
            click.echo(f'\nBootstrap synthetic bets: {bootstrap_total}')

            # 2. Count pick contexts with polluted matchup features
            all_contexts = (
                db.session.query(Bet, PickContext)
                .join(PickContext, Bet.id == PickContext.bet_id)
                .filter(Bet.outcome.in_(['win', 'lose']))
                .all()
            )
            polluted_count = 0
            polluted_bootstrap = 0
            polluted_auto = 0
            clean_count = 0
            matchup_keys = ('opp_defense_rating', 'opp_pace', 'opp_matchup_adj')

            for bet_obj, pick_ctx in all_contexts:
                try:
                    ctx = _json.loads(pick_ctx.context_json) if pick_ctx.context_json else {}
                except (ValueError, TypeError):
                    polluted_count += 1
                    continue
                zeroed = sum(1 for k in matchup_keys if float(ctx.get(k, 0) or 0) == 0)
                if zeroed == len(matchup_keys):
                    polluted_count += 1
                    if (bet_obj.notes or '').startswith('AUTO_BOOTSTRAP_HIDDEN'):
                        polluted_bootstrap += 1
                    elif bet_obj.source == 'auto_generated':
                        polluted_auto += 1
                else:
                    clean_count += 1

            click.echo(f'Resolved bets with PickContext: {len(all_contexts)}')
            click.echo(f'  Clean (real matchup data): {clean_count}')
            click.echo(f'  Polluted (zeroed matchup): {polluted_count}')
            click.echo(f'    - Bootstrap synthetic: {polluted_bootstrap}')
            click.echo(f'    - Auto picks (no opponent): {polluted_auto}')
            click.echo(f'    - Other: {polluted_count - polluted_bootstrap - polluted_auto}')

            # 3. Model metadata audit
            active_models = ModelMetadata.query.filter_by(is_active=True).all()
            inactive_models = ModelMetadata.query.filter_by(is_active=False).count()
            click.echo(f'\nActive models: {len(active_models)}')
            click.echo(f'Inactive (historical) models: {inactive_models}')
            for m in active_models:
                click.echo(
                    f'  {m.model_name} v{m.version} | samples={m.training_samples} | '
                    f'mae={m.val_mae} | acc={m.val_accuracy} | '
                    f'trained={m.training_date.isoformat() if m.training_date else "n/a"}'
                )

            # 4. Pollution ratio
            if len(all_contexts) > 0:
                ratio = polluted_count / len(all_contexts)
                click.echo(f'\nPollution ratio: {ratio:.1%}')
                if ratio > 0.3:
                    click.echo('CRITICAL: >30% of training data is polluted — model drift is expected.')
                elif ratio > 0.1:
                    click.echo('WARNING: >10% pollution — model accuracy degraded.')
                else:
                    click.echo('OK: pollution level is manageable.')

            if not fix:
                click.echo('\nRun with --fix to delete polluted bootstrap rows and deactivate stale models.')
                return

            # === FIX MODE ===
            click.echo('\n=== Cleaning Polluted Data ===')

            # Delete polluted bootstrap bets (and cascaded PickContext via FK)
            deleted_bootstrap = 0
            bootstrap_bets = (
                Bet.query
                .filter(Bet.notes.like('AUTO_BOOTSTRAP_HIDDEN%'))
                .all()
            )
            for bet_obj in bootstrap_bets:
                # Check if its context is polluted
                pc = PickContext.query.filter_by(bet_id=bet_obj.id).first()
                if pc:
                    try:
                        ctx = _json.loads(pc.context_json) if pc.context_json else {}
                    except (ValueError, TypeError):
                        ctx = {}
                    zeroed = sum(1 for k in matchup_keys if float(ctx.get(k, 0) or 0) == 0)
                    if zeroed == len(matchup_keys):
                        db.session.delete(bet_obj)
                        deleted_bootstrap += 1

            db.session.commit()
            click.echo(f'Deleted {deleted_bootstrap} polluted bootstrap bets (+ cascaded PickContext).')

            # Deactivate stale pick-quality models trained on polluted data
            pq_models = ModelMetadata.query.filter_by(
                model_name='pick_quality_nba', is_active=True,
            ).all()
            deactivated = 0
            for m in pq_models:
                m.is_active = False
                deactivated += 1
            db.session.commit()
            click.echo(f'Deactivated {deactivated} pick_quality_nba model(s) trained on polluted data.')

            if retrain_after:
                from app.services.pick_quality_model import train_pick_quality_model
                click.echo('Retraining Model 2 on clean data...')
                result = train_pick_quality_model()
                click.echo(f'Retrain result: {result}')

            click.echo('Done.')

    app.cli.add_command(cli_pollution_report)

    # ──────────────────────────────────────────────────────────────────
    # Postmortem commands
    # ──────────────────────────────────────────────────────────────────

    @app.cli.command('backfill-postmortems')
    @click.option(
        '--days', default=90, show_default=True,
        help='Analyse settled bets from the last N days.',
    )
    @click.option(
        '--overwrite', is_flag=True, default=False,
        help='Re-analyse bets that already have a postmortem (update in place).',
    )
    @click.option(
        '--dry-run', is_flag=True, default=False,
        help='Show counts without writing to the database.',
    )
    def cli_backfill_postmortems(days, overwrite, dry_run):
        """Backfill postmortem records for already-settled player-prop bets.

        Safely skips:
        - Non-prop bets (moneyline, over/under totals)
        - Pushes / DNPs (no useful analysis)
        - Pending bets
        - Bets with insufficient data (no actual_total)

        Run this after deploying the postmortem system to analyse historical data::

            flask backfill-postmortems --days 60
        """
        from app.services.postmortem_service import backfill_postmortems
        from app.enums import Outcome

        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        settled = (
            Bet.query
            .filter(Bet.outcome.in_([Outcome.WIN.value, Outcome.LOSE.value]))
            .filter(Bet.match_date >= cutoff)
            .filter(Bet.actual_total.isnot(None))
            .all()
        )

        click.echo(
            f'Found {len(settled)} settled bets in last {days} days with actual results.'
        )

        if dry_run:
            prop_count = sum(1 for b in settled if b.is_player_prop)
            existing = sum(1 for b in settled if b.is_player_prop and b.postmortem)
            click.echo(
                f'Dry-run: would analyse {prop_count} player-prop bets '
                f'({existing} already have postmortems; '
                f'{"overwriting" if overwrite else "skipping"} those).'
            )
            return

        summary = backfill_postmortems(settled, skip_existing=not overwrite)
        click.echo(
            f"Postmortem backfill complete: "
            f"created={summary['created']} "
            f"skipped={summary['skipped']} "
            f"ineligible={summary['ineligible']} "
            f"errors={summary['errors']}"
        )

    @app.cli.command('postmortem-report')
    @click.option('--days', default=30, show_default=True,
                  help='Report window in days.')
    @click.option('--min-count', default=3, show_default=True,
                  help='Minimum postmortems per reason to include in report.')
    def cli_postmortem_report(days, min_count):
        """Print a summary of postmortem reason-code frequency for recent bets.

        Shows what loss/win patterns are most common so you can identify
        systematic model weaknesses::

            flask postmortem-report --days 60
        """
        from app.models import BetPostmortem
        from collections import Counter

        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        pms = (
            BetPostmortem.query
            .filter(BetPostmortem.created_at >= cutoff)
            .all()
        )

        if not pms:
            click.echo(f'No postmortems found in the last {days} days.')
            return

        joined = (
            db.session.query(BetPostmortem, Bet)
            .join(Bet, BetPostmortem.bet_id == Bet.id)
            .filter(BetPostmortem.created_at >= cutoff)
            .all()
        )

        total = len(joined)
        losses = [(pm, b) for pm, b in joined if b.outcome == 'lose']
        wins = [(pm, b) for pm, b in joined if b.outcome == 'win']

        click.echo(f'\n=== Postmortem Report — Last {days} days ===')
        click.echo(f'Total analysed: {total}  |  Losses: {len(losses)}  |  Wins: {len(wins)}')

        # Reason distribution for losses
        loss_reasons = Counter(
            pm.primary_reason_code for pm, _b in losses if pm.primary_reason_code
        )
        click.echo('\nTop loss reasons (primary):')
        for reason, count in loss_reasons.most_common():
            if count >= min_count:
                pct = count / max(len(losses), 1) * 100
                click.echo(f'  {reason:<35} {count:>4}  ({pct:.0f}%)')

        # Average projection error by reason
        click.echo('\nAvg projection error by primary reason (losses):')
        by_reason: dict = {}
        for pm, _b in losses:
            if pm.primary_reason_code and pm.projection_error is not None:
                by_reason.setdefault(pm.primary_reason_code, []).append(pm.projection_error)
        for reason, errs in sorted(by_reason.items(), key=lambda x: abs(sum(x[1])/len(x[1])), reverse=True):
            if len(errs) >= min_count:
                avg = sum(errs) / len(errs)
                click.echo(f'  {reason:<35} avg_err={avg:+.2f}  n={len(errs)}')

    # ──────────────────────────────────────────────────────────────────
    # Model accuracy / production readiness commands
    # ──────────────────────────────────────────────────────────────────

    @app.cli.command('model_accuracy')
    @click.option('--days', type=int, default=90, show_default=True,
                  help='Rolling window in days.')
    @click.option('--stat-type', default=None,
                  help='Limit report to a single stat type (e.g. player_points).')
    def cli_model_accuracy(days, stat_type):
        """Rolling live MAE for Model 1 projections using BetPostmortem data.

        Joins BetPostmortem.projected_stat vs BetPostmortem.actual_stat to
        compute per-stat-type mean absolute error from real settled bets.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        query = (
            db.session.query(BetPostmortem)
            .filter(BetPostmortem.created_at >= cutoff)
            .filter(BetPostmortem.projected_stat.isnot(None))
            .filter(BetPostmortem.actual_stat.isnot(None))
        )
        if stat_type:
            query = query.filter(BetPostmortem.stat_type == stat_type)

        rows = query.all()

        click.echo(f'=== Model 1 Live Accuracy (last {days} days) ===')
        if not rows:
            click.echo('No postmortem data with projections yet.')
            return

        # Group by stat_type
        by_stat: dict = {}
        for pm in rows:
            key = pm.stat_type or 'unknown'
            by_stat.setdefault(key, []).append(abs((pm.actual_stat or 0.0) - (pm.projected_stat or 0.0)))

        # Expected MAE thresholds per stat type (healthy ranges)
        thresholds = {
            'player_points': 3.5,
            'player_rebounds': 2.0,
            'player_assists': 2.0,
            'player_threes': 1.5,
            'player_steals': 1.0,
            'player_blocks': 1.0,
        }

        click.echo(f'{"Stat Type":<30} {"N":>5} {"MAE":>7} {"Threshold":>10} {"Status":>8}')
        click.echo('-' * 65)
        for stype, errors in sorted(by_stat.items()):
            mae = sum(errors) / len(errors)
            threshold = thresholds.get(stype, 3.5)
            status = 'OK' if mae <= threshold else 'WARN'
            click.echo(f'{stype:<30} {len(errors):>5} {mae:>7.3f} {threshold:>10.1f} {status:>8}')

        total_errors = [e for errs in by_stat.values() for e in errs]
        overall_mae = sum(total_errors) / len(total_errors)
        click.echo(f'\nOverall MAE across all stat types: {overall_mae:.3f}  (n={len(total_errors)})')

        # Compare against val_mae from ModelMetadata
        click.echo('\n=== vs Training val_mae ===')
        for stype in sorted(by_stat.keys()):
            model_meta = (
                ModelMetadata.query
                .filter_by(model_name=f'projection_{stype}', is_active=True)
                .first()
            )
            live_mae = sum(by_stat[stype]) / len(by_stat[stype])
            if model_meta and model_meta.val_mae:
                gap = live_mae - model_meta.val_mae
                click.echo(
                    f'  {stype:<30} live={live_mae:.3f}  val={model_meta.val_mae:.3f}  '
                    f'gap={gap:+.3f}'
                    + ('  WARN: live >> val' if gap > 1.0 else '')
                )
            else:
                click.echo(f'  {stype:<30} live={live_mae:.3f}  val=n/a (no active model)')

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
            def _fmt(label, seg):
                if seg is None:
                    click.echo(f'  {label}: no data')
                else:
                    count, wins, rate = seg
                    click.echo(f'  {label}: {rate:.1%} ({wins}/{count})')

            _fmt('Manual bets', win_rate_result['manual'])
            _fmt('Auto picks (real)', win_rate_result['auto'])
            real = win_rate_result['real']
            if real:
                count, wins_30d, rolling_win_rate = real
                click.echo(f'  Rolling win rate: {rolling_win_rate:.3f} ({wins_30d}/{count})')
            _fmt('Bootstrap synthetic', win_rate_result['bootstrap'])

            pq_model = ModelMetadata.query.filter_by(
                model_name='pick_quality_nba', is_active=True,
            ).first()
            if real and pq_model and pq_model.val_accuracy:
                delta = rolling_win_rate - pq_model.val_accuracy
                click.echo(
                    f'  vs model val_accuracy ({pq_model.val_accuracy:.3f}): '
                    f'delta={delta:+.3f}'
                )
                if abs(delta) > 0.05:
                    click.echo('  WARN: >5% drift detected — consider retraining.')
            elif not real:
                click.echo('  No real (non-bootstrap) resolved bets in last 30 days.')
            else:
                click.echo('  No active pick_quality model metadata for comparison.')
        else:
            click.echo('No resolved bets with context in last 30 days.')

        # Last automated drift check result
        click.echo('\n=== Last Automated Drift Check ===')
        last_drift = (
            JobLog.query
            .filter_by(job_name='drift_check')
            .order_by(JobLog.started_at.desc())
            .first()
        )
        if last_drift:
            ts = last_drift.started_at.isoformat() if last_drift.started_at else 'n/a'
            click.echo(f'  Last run: {ts}  status={last_drift.status}')
            if last_drift.message:
                click.echo(f'  Message: {last_drift.message}')
        else:
            click.echo('  No drift check job log found.')

        click.echo('\n=== Recent JobLog entries ===')
        jobs = JobLog.query.order_by(JobLog.started_at.desc()).limit(20).all()
        if not jobs:
            click.echo('No job log records found.')
        for job in jobs:
            click.echo(
                f"- {job.started_at.isoformat() if job.started_at else 'n/a'} | {job.job_name} | "
                f"status={job.status} | msg={job.message or ''}"
            )

    @app.cli.command('prod-readiness')
    def cli_prod_readiness():
        """Production readiness checklist for models and data freshness.

        Prints a formatted checklist covering:
        - Model 1 val_mae per stat type (vs healthy thresholds)
        - Model 2 resolved picks count and last drift check
        - TeamDefenseSnapshot and OddsSnapshot staleness
        - DB index coverage (key composite indexes present)
        """
        import os
        from zoneinfo import ZoneInfo

        now_et = datetime.now(ZoneInfo("America/New_York"))
        today = now_et.date()

        click.echo('=== Production Readiness Report ===')
        click.echo(f'Generated: {now_et.isoformat()}')
        click.echo('')

        checks = []  # list of (label, status, detail)

        # ── Model 1: val_mae per stat ──────────────────────────────────
        click.echo('--- Model 1: Projection val_mae ---')
        stat_thresholds = {
            'player_points': 3.5,
            'player_rebounds': 2.0,
            'player_assists': 2.0,
            'player_threes': 1.5,
            'player_steals': 1.0,
            'player_blocks': 1.0,
        }
        active_proj = (
            ModelMetadata.query
            .filter(ModelMetadata.model_name.like('projection_%'))
            .filter_by(is_active=True)
            .all()
        )
        if not active_proj:
            click.echo('  FAIL  No active projection models found')
            checks.append(('Model 1 models', 'FAIL', 'no active models'))
        else:
            for m in active_proj:
                stype = m.model_name.replace('projection_', '')
                threshold = stat_thresholds.get(stype, 3.5)
                if m.val_mae is None:
                    status = 'WARN'
                    detail = f'{m.model_name}: val_mae=None'
                elif m.val_mae <= threshold:
                    status = 'OK  '
                    detail = f'{m.model_name}: val_mae={m.val_mae:.3f} <= {threshold}'
                else:
                    status = 'WARN'
                    detail = f'{m.model_name}: val_mae={m.val_mae:.3f} > threshold {threshold}'
                click.echo(f'  {status}  {detail}')
                checks.append((f'Model1:{stype}', status.strip(), detail))

        # ── Model 2: resolved picks + drift ───────────────────────────
        click.echo('\n--- Model 2: Pick Quality ---')
        resolved_count = (
            db.session.query(Bet)
            .join(PickContext, Bet.id == PickContext.bet_id)
            .filter(Bet.outcome.in_(['win', 'lose']))
            .count()
        )
        MIN_PROD_SAFE = 300
        if resolved_count >= MIN_PROD_SAFE:
            m2_count_status = 'OK  '
        elif resolved_count >= 200:
            m2_count_status = 'WARN'
        else:
            m2_count_status = 'FAIL'
        click.echo(
            f'  {m2_count_status}  Resolved picks with context: {resolved_count} '
            f'(prod-safe threshold: {MIN_PROD_SAFE})'
        )
        checks.append(('Model2:resolved_count', m2_count_status.strip(), f'{resolved_count} resolved'))

        pq_model = ModelMetadata.query.filter_by(model_name='pick_quality_nba', is_active=True).first()
        if pq_model:
            train_dt = _as_utc(pq_model.training_date)
            trained_days_ago = (datetime.now(timezone.utc) - train_dt).days if train_dt else None
            acc_str = f'{pq_model.val_accuracy:.3f}' if pq_model.val_accuracy else 'n/a'
            age_str = f'{trained_days_ago}d ago' if trained_days_ago is not None else 'unknown'
            click.echo(f'  OK    pick_quality_nba active | val_accuracy={acc_str} | trained {age_str}')
            checks.append(('Model2:active', 'OK', f'val_accuracy={acc_str}'))
        else:
            click.echo('  WARN  No active pick_quality_nba model')
            checks.append(('Model2:active', 'WARN', 'no active model'))

        # Last drift check
        last_drift = (
            JobLog.query
            .filter_by(job_name='drift_check')
            .order_by(JobLog.started_at.desc())
            .first()
        )
        if last_drift:
            drift_dt = _as_utc(last_drift.started_at)
            drift_age = (datetime.now(timezone.utc) - drift_dt).days if drift_dt else None
            drift_status = 'OK  ' if last_drift.status in ('success', 'warn') else 'WARN'
            drift_detail = f'last drift check: {drift_age}d ago, status={last_drift.status}'
            click.echo(f'  {drift_status}  {drift_detail}')
            if last_drift.status == 'warn' and last_drift.message:
                click.echo(f'        Drift message: {last_drift.message[:120]}')
            checks.append(('Model2:drift', drift_status.strip(), drift_detail))
        else:
            click.echo('  WARN  No drift check job found — weekly drift monitor may not have run yet')
            checks.append(('Model2:drift', 'WARN', 'no drift check found'))

        # ── Data freshness ─────────────────────────────────────────────
        click.echo('\n--- Data Freshness ---')

        # TeamDefenseSnapshot
        latest_defense = (
            db.session.query(func.max(TeamDefenseSnapshot.snapshot_date))
            .scalar()
        )
        if latest_defense is None:
            def_status = 'FAIL'
            def_detail = 'no TeamDefenseSnapshot rows'
        else:
            days_old = (today - latest_defense).days
            if days_old <= 2:
                def_status = 'OK  '
            elif days_old <= 7:
                def_status = 'WARN'
            else:
                def_status = 'FAIL'
            def_detail = f'latest TeamDefenseSnapshot: {latest_defense} ({days_old}d old)'
        click.echo(f'  {def_status}  {def_detail}')
        checks.append(('TeamDefenseSnapshot', def_status.strip(), def_detail))

        # OddsSnapshot
        latest_odds = (
            db.session.query(func.max(OddsSnapshot.game_date))
            .scalar()
        )
        if latest_odds is None:
            odds_status = 'WARN'
            odds_detail = 'no OddsSnapshot rows (line movement features unavailable)'
        else:
            days_old = (today - latest_odds).days
            odds_status = 'OK  ' if days_old <= 3 else 'WARN'
            odds_detail = f'latest OddsSnapshot: {latest_odds} ({days_old}d old)'
        click.echo(f'  {odds_status}  {odds_detail}')
        checks.append(('OddsSnapshot', odds_status.strip(), odds_detail))

        # PlayerGameLog freshness
        latest_log = (
            db.session.query(func.max(PlayerGameLog.game_date))
            .scalar()
        )
        if latest_log is None:
            log_status = 'FAIL'
            log_detail = 'no PlayerGameLog rows'
        else:
            days_old = (today - latest_log).days
            log_status = 'OK  ' if days_old <= 2 else ('WARN' if days_old <= 7 else 'FAIL')
            log_detail = f'latest PlayerGameLog: {latest_log} ({days_old}d old)'
        click.echo(f'  {log_status}  {log_detail}')
        checks.append(('PlayerGameLog', log_status.strip(), log_detail))

        # ── Summary ────────────────────────────────────────────────────
        click.echo('\n--- Summary ---')
        fails = [c for c in checks if c[1] == 'FAIL']
        warns = [c for c in checks if c[1] == 'WARN']
        oks = [c for c in checks if c[1] == 'OK']
        click.echo(f'  OK: {len(oks)}  WARN: {len(warns)}  FAIL: {len(fails)}')
        if fails:
            click.echo('VERDICT: NOT READY FOR PRODUCTION')
            for label, _status, detail in fails:
                click.echo(f'  FAIL [{label}] {detail}')
        elif warns:
            click.echo('VERDICT: CAUTION — address warnings before high-stakes use')
            for label, _status, detail in warns:
                click.echo(f'  WARN [{label}] {detail}')
        else:
            click.echo('VERDICT: PRODUCTION READY')

        ml_enabled = os.getenv('USE_ML_PROJECTIONS', 'false').lower() == 'true'
        m2_enabled = os.getenv('MODEL2_TIME_AWARE_SPLIT', 'false').lower() == 'true'
        click.echo(f'\n  USE_ML_PROJECTIONS={ml_enabled}  MODEL2_TIME_AWARE_SPLIT={m2_enabled}')
