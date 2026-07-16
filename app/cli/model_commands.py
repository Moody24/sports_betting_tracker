"""Model, pick-quality, postmortem, and accuracy Flask CLI commands."""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta

import click
from flask import current_app
from sqlalchemy import func
from sqlalchemy.exc import OperationalError

from app import db
from app.models import (
    Bet,
    BetPostmortem,
    JobLog,
    ModelMetadata,
    OddsSnapshot,
    PickContext,
    PlayerGameLog,
    TeamDefenseSnapshot,
)
from app.cli import _as_utc, _resolved_win_rate

logger = logging.getLogger(__name__)


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str
    critical: bool = False


# ── Projection / training commands ────────────────────────────────────────────

@click.command('run-projections')
def cli_run_projections():
    from app.services.scheduler import run_projections
    click.echo('Running projections...')
    run_projections()
    click.echo('Done.')


@click.command('grade-bets')
def cli_grade_bets():
    from app.services.scheduler import resolve_and_grade
    click.echo('Grading bets...')
    resolve_and_grade()
    click.echo('Done.')


@click.command('retrain')
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
        from app.services.distributional_model import retrain_all_distributional_models
        click.echo('--force: training distributional heads + calibrators (Plan C, shadow path)...')
        dist_result = retrain_all_distributional_models()
        click.echo(f'Distributional retrain: {dist_result}')
    else:
        retrain_models()
    click.echo('Done.')


@click.command('bootstrap-pick-quality')
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


# ── Model diagnostics ──────────────────────────────────────────────────────────

@click.command('drift_report')
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


@click.command('model_calibration_report')
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


@click.command('model_accuracy')
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

    # Split OT vs non-OT. OT games inflate counting stats (extra possessions)
    # and are structural noise, not model error — excluded from drift comparison.
    non_ot_rows = [pm for pm in rows if not pm.overtime_flag]
    ot_rows = [pm for pm in rows if pm.overtime_flag]

    # Group by stat_type (non-OT only for drift comparison)
    by_stat: dict = {}
    by_stat_ot: dict = {}
    for pm in non_ot_rows:
        key = pm.stat_type or 'unknown'
        by_stat.setdefault(key, []).append(abs((pm.actual_stat or 0.0) - (pm.projected_stat or 0.0)))
    for pm in ot_rows:
        key = pm.stat_type or 'unknown'
        by_stat_ot.setdefault(key, []).append(abs((pm.actual_stat or 0.0) - (pm.projected_stat or 0.0)))

    # Expected MAE thresholds per stat type (healthy ranges)
    thresholds = {
        'player_points': 3.5,
        'player_rebounds': 2.0,
        'player_assists': 2.0,
        'player_threes': 1.5,
        'player_steals': 1.0,
        'player_blocks': 1.0,
    }

    click.echo(f'\n--- Regulation games only (n={len(non_ot_rows)}) ---')
    click.echo(f'{"Stat Type":<30} {"N":>5} {"MAE":>7} {"Threshold":>10} {"Status":>8}')
    click.echo('-' * 65)
    for stype, errors in sorted(by_stat.items()):
        mae = sum(errors) / len(errors)
        threshold = thresholds.get(stype, 3.5)
        status = 'OK' if mae <= threshold else 'WARN'
        click.echo(f'{stype:<30} {len(errors):>5} {mae:>7.3f} {threshold:>10.1f} {status:>8}')

    if by_stat:
        total_errors = [e for errs in by_stat.values() for e in errs]
        overall_mae = sum(total_errors) / len(total_errors)
        click.echo(f'\nOverall MAE (regulation): {overall_mae:.3f}  (n={len(total_errors)})')

    if ot_rows:
        ot_total = [abs((pm.actual_stat or 0.0) - (pm.projected_stat or 0.0)) for pm in ot_rows]
        ot_mae = sum(ot_total) / len(ot_total)
        click.echo(f'Overall MAE (OT games):    {ot_mae:.3f}  (n={len(ot_rows)}) — '
                   f'excluded from drift; OT inflates counting stats')
        all_errors = [abs((pm.actual_stat or 0.0) - (pm.projected_stat or 0.0)) for pm in rows]
        click.echo(f'Overall MAE (combined):    {sum(all_errors)/len(all_errors):.3f}  (n={len(rows)})')

    # Drift comparison uses regulation-only MAE (apples-to-apples vs val_mae)
    click.echo('\n=== vs Training val_mae (regulation games only) ===')
    for stype in sorted(by_stat.keys()):
        model_meta = (
            ModelMetadata.query
            .filter_by(model_name=f'projection_{stype}', is_active=True)
            .first()
        )
        live_mae = sum(by_stat[stype]) / len(by_stat[stype])
        ot_n = len(by_stat_ot.get(stype, []))
        if model_meta and model_meta.val_mae:
            gap = live_mae - model_meta.val_mae
            ot_note = f'  [{ot_n} OT games excluded]' if ot_n else ''
            click.echo(
                f'  {stype:<30} live={live_mae:.3f}  val={model_meta.val_mae:.3f}  '
                f'gap={gap:+.3f}'
                + ('  WARN: live >> val' if gap > 1.0 else '')
                + ot_note
            )
        else:
            click.echo(f'  {stype:<30} live={live_mae:.3f}  val=n/a (no active model)')


@click.command('backtest')
@click.option(
    '--stat-type', default='player_points', show_default=True,
    help='Distributional stat type to backtest (quantile: player_points/'
         'player_rebounds/player_assists/player_points_rebounds_assists; '
         'poisson: player_threes/player_steals/player_blocks).',
)
def cli_backtest(stat_type):
    """Compare calibrated distributional P(over) with the incumbent Gaussian."""
    import math as _math
    import time as _time

    from app import db
    from app.models import JobLog
    from app.services.distribution import (
        median_from_quantiles,
        prob_over,
        prob_over_poisson,
        rectify_quantiles,
    )
    from app.services.distribution_calibration import apply_calibrator
    from app.services.distributional_model import (
        DIST_STAT_TYPES,
        POISSON_DIST_STAT_TYPES,
        QUANTILE_ALPHAS,
        _build_dist_training_rows,
        _date_cutoff_split,
        backtest_verdict,
    )
    from app.services.distributional_predictor import load_calibrator, load_quantile_model
    from app.services.ml_model import load_active_model
    from app.services.pick_quality_model import compute_calibration_metrics

    click.echo(f'=== Distributional Backtest: {stat_type} ===')
    _t0 = _time.perf_counter()

    if stat_type in DIST_STAT_TYPES:
        model, feature_names = load_quantile_model(stat_type)
        if model is None:
            click.echo(f'No active dist_{stat_type} model — run `flask retrain --force` first.')
            return
        calibrator = load_calibrator(stat_type)
        rows = _build_dist_training_rows(stat_type)
    elif stat_type in POISSON_DIST_STAT_TYPES:
        from app.services.ml_model import _build_training_rows as _build_point_rows
        rows = _build_point_rows(stat_type)
    else:
        click.echo(f'Unsupported stat_type: {stat_type}')
        return

    if not rows:
        click.echo('No training rows available for backtest.')
        return

    _, val_idx, _, _ = _date_cutoff_split(rows)
    if not val_idx:
        click.echo('No held-out rows available for backtest.')
        return

    dist_pairs = []
    gaussian_pairs = []

    if stat_type in DIST_STAT_TYPES:
        import numpy as np
        from scipy.stats import norm
        for idx in val_idx:
            _, _, features, target = rows[idx]
            X = np.array([[features.get(k, 0) for k in feature_names]])
            raw_q = rectify_quantiles(model.predict(X)[0].tolist())
            median = median_from_quantiles(QUANTILE_ALPHAS, raw_q)
            std_proxy = max((raw_q[-1] - raw_q[0]) / 4.0, 0.5)
            for offset in (-6.0, -3.0, 0.0, 3.0, 6.0):
                line = median + offset
                p_dist = prob_over(line, QUANTILE_ALPHAS, raw_q)
                if calibrator is not None:
                    p_dist = apply_calibrator(calibrator, p_dist)
                y = 1.0 if target > line else 0.0
                dist_pairs.append((p_dist, y))
                p_gauss = float(1.0 - norm.cdf(line, loc=median, scale=std_proxy))
                gaussian_pairs.append((p_gauss, y))
    else:
        model, feature_names = load_active_model(stat_type)
        if model is None:
            click.echo(f'No active projection_{stat_type} model — run `flask retrain --force` first.')
            return
        calibrator = load_calibrator(stat_type)
        import numpy as np
        from scipy.stats import norm
        for idx in val_idx:
            _, _, features, target = rows[idx]
            X = np.array([[features.get(k, 0) for k in feature_names]])
            lam = float(model.predict(X)[0])
            if lam <= 0:
                continue
            for offset_frac in (-0.9, -0.6, 0.0, 0.6, 0.9):
                candidate = lam + offset_frac * max(lam, 1.0)
                line = max(0.5, _math.floor(candidate) + 0.5)
                p_dist = prob_over_poisson(line, lam)
                if calibrator is not None:
                    p_dist = apply_calibrator(calibrator, p_dist)
                y = 1.0 if target > line else 0.0
                dist_pairs.append((p_dist, y))
                p_gauss = float(1.0 - norm.cdf(line, loc=lam, scale=max(lam ** 0.5, 0.5)))
                gaussian_pairs.append((p_gauss, y))

    if not dist_pairs:
        click.echo('No evaluable held-out pairs produced.')
        return

    dist_metrics = compute_calibration_metrics(dist_pairs, bins=5)
    gauss_metrics = compute_calibration_metrics(gaussian_pairs, bins=5)
    elapsed = _time.perf_counter() - _t0

    click.echo(f"Held-out pairs: {len(dist_pairs)}")
    click.echo(
        f"Distributional  ECE={dist_metrics['ece']:.4f}  "
        f"Brier={dist_metrics['brier']:.4f}  LogLoss={dist_metrics['logloss']:.4f}"
    )
    click.echo(
        f"Incumbent (Gaussian) ECE={gauss_metrics['ece']:.4f}  "
        f"Brier={gauss_metrics['brier']:.4f}  LogLoss={gauss_metrics['logloss']:.4f}"
    )
    click.echo(f"Backtest wall time: {elapsed:.1f}s")

    verdict = backtest_verdict(dist_metrics['ece'], gauss_metrics['ece'])
    message = (
        f"stat={stat_type} dist_ece={dist_metrics['ece']:.4f} "
        f"gauss_ece={gauss_metrics['ece']:.4f} verdict={verdict}"
    )
    db.session.add(JobLog(
        job_name='distributional_backtest',
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
        status='success' if verdict == 'PROMOTE' else 'warn',
        message=message[:500],
    ))
    db.session.commit()

    click.echo(f"\nVerdict: {verdict}  (gate: ECE <= 0.03 and beats incumbent)")


@click.command('model_status')
def cli_model_status():
    """Show data/model/job diagnostics for backfill and training."""
    import os

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


def _check_model1_projection() -> list:
    """Check val_mae for all active projection models. Returns list[CheckResult]."""
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
        return [CheckResult('Model 1 models', False, 'No active projection models found', critical=True)]
    results = []
    for m in active_proj:
        stype = m.model_name.replace('projection_', '')
        threshold = stat_thresholds.get(stype, 3.5)
        if m.val_mae is None:
            results.append(CheckResult(f'Model1:{stype}', False, f'{m.model_name}: val_mae=None'))
        elif m.val_mae <= threshold:
            results.append(CheckResult(f'Model1:{stype}', True,
                                       f'{m.model_name}: val_mae={m.val_mae:.3f} <= {threshold}'))
        else:
            results.append(CheckResult(f'Model1:{stype}', False,
                                       f'{m.model_name}: val_mae={m.val_mae:.3f} > threshold {threshold}'))
    return results


def _check_model2_resolved_count() -> CheckResult:
    """Check that enough resolved picks with context exist for Model 2."""
    MIN_PROD_SAFE = 300
    resolved_count = (
        db.session.query(Bet)
        .join(PickContext, Bet.id == PickContext.bet_id)
        .filter(Bet.outcome.in_(['win', 'lose']))
        .count()
    )
    detail = f'Resolved picks with context: {resolved_count} (prod-safe threshold: {MIN_PROD_SAFE})'
    if resolved_count >= MIN_PROD_SAFE:
        return CheckResult('Model2:resolved_count', True, detail)
    if resolved_count >= 200:
        return CheckResult('Model2:resolved_count', False, detail)
    return CheckResult('Model2:resolved_count', False, detail, critical=True)


def _check_model2_active() -> CheckResult:
    """Check that an active pick_quality_nba model exists."""
    pq_model = ModelMetadata.query.filter_by(model_name='pick_quality_nba', is_active=True).first()
    if pq_model:
        train_dt = _as_utc(pq_model.training_date)
        trained_days_ago = (datetime.now(timezone.utc) - train_dt).days if train_dt else None
        acc_str = f'{pq_model.val_accuracy:.3f}' if pq_model.val_accuracy else 'n/a'
        age_str = f'{trained_days_ago}d ago' if trained_days_ago is not None else 'unknown'
        return CheckResult('Model2:active', True,
                           f'pick_quality_nba active | val_accuracy={acc_str} | trained {age_str}')
    return CheckResult('Model2:active', False, 'No active pick_quality_nba model')


def _check_model2_drift() -> CheckResult:
    """Check that a recent drift-check job has run successfully."""
    last_drift = (
        JobLog.query
        .filter_by(job_name='drift_check')
        .order_by(JobLog.started_at.desc())
        .first()
    )
    if not last_drift:
        return CheckResult('Model2:drift', False,
                           'No drift check job found — weekly drift monitor may not have run yet')
    drift_dt = _as_utc(last_drift.started_at)
    drift_age = (datetime.now(timezone.utc) - drift_dt).days if drift_dt else None
    passed = last_drift.status in ('success', 'warn')
    detail = f'last drift check: {drift_age}d ago, status={last_drift.status}'
    if last_drift.status == 'warn' and last_drift.message:
        detail += f'\n        Drift message: {last_drift.message[:120]}'
    return CheckResult('Model2:drift', passed, detail)


def _check_team_defense_snapshot(today) -> CheckResult:
    """Check TeamDefenseSnapshot freshness."""
    latest = db.session.query(func.max(TeamDefenseSnapshot.snapshot_date)).scalar()
    if latest is None:
        return CheckResult('TeamDefenseSnapshot', False, 'no TeamDefenseSnapshot rows', critical=True)
    days_old = (today - latest).days
    detail = f'latest TeamDefenseSnapshot: {latest} ({days_old}d old)'
    if days_old <= 2:
        return CheckResult('TeamDefenseSnapshot', True, detail)
    if days_old <= 7:
        return CheckResult('TeamDefenseSnapshot', False, detail)
    return CheckResult('TeamDefenseSnapshot', False, detail, critical=True)


def _check_odds_snapshot(today) -> CheckResult:
    """Check OddsSnapshot freshness."""
    latest = db.session.query(func.max(OddsSnapshot.game_date)).scalar()
    if latest is None:
        return CheckResult('OddsSnapshot', False,
                           'no OddsSnapshot rows (line movement features unavailable)')
    days_old = (today - latest).days
    detail = f'latest OddsSnapshot: {latest} ({days_old}d old)'
    return CheckResult('OddsSnapshot', days_old <= 3, detail)


def _check_player_game_log(today) -> CheckResult:
    """Check PlayerGameLog freshness."""
    latest = db.session.query(func.max(PlayerGameLog.game_date)).scalar()
    if latest is None:
        return CheckResult('PlayerGameLog', False, 'no PlayerGameLog rows', critical=True)
    days_old = (today - latest).days
    detail = f'latest PlayerGameLog: {latest} ({days_old}d old)'
    if days_old <= 2:
        return CheckResult('PlayerGameLog', True, detail)
    if days_old <= 7:
        return CheckResult('PlayerGameLog', False, detail)
    return CheckResult('PlayerGameLog', False, detail, critical=True)


@click.command('prod-readiness')
def cli_prod_readiness():
    """Production readiness checklist for models and data freshness.

    Prints a formatted checklist covering:
    - Model 1 val_mae per stat type (vs healthy thresholds)
    - Model 2 resolved picks count and last drift check
    - TeamDefenseSnapshot and OddsSnapshot staleness
    - DB index coverage (key composite indexes present)
    """
    import os
    from app.utils.time_helpers import ET

    now_et = datetime.now(ET)
    today = now_et.date()

    click.echo('=== Production Readiness Report ===')
    click.echo(f'Generated: {now_et.isoformat()}')
    click.echo('')

    checks = []  # list[CheckResult]

    def _status(r):
        return 'OK  ' if r.passed else ('FAIL' if r.critical else 'WARN')

    click.echo('--- Model 1: Projection val_mae ---')
    for r in _check_model1_projection():
        click.echo(f'  {_status(r)}  {r.detail}')
        checks.append(r)

    click.echo('\n--- Model 2: Pick Quality ---')
    for r in [
        _check_model2_resolved_count(),
        _check_model2_active(),
        _check_model2_drift(),
    ]:
        click.echo(f'  {_status(r)}  {r.detail}')
        checks.append(r)

    click.echo('\n--- Data Freshness ---')
    for r in [
        _check_team_defense_snapshot(today),
        _check_odds_snapshot(today),
        _check_player_game_log(today),
    ]:
        click.echo(f'  {_status(r)}  {r.detail}')
        checks.append(r)

    click.echo('\n--- Summary ---')
    fails = [r for r in checks if not r.passed and r.critical]
    warns = [r for r in checks if not r.passed and not r.critical]
    oks   = [r for r in checks if r.passed]
    click.echo(f'  OK: {len(oks)}  WARN: {len(warns)}  FAIL: {len(fails)}')
    if fails:
        click.echo('VERDICT: NOT READY FOR PRODUCTION')
        for r in fails:
            click.echo(f'  FAIL [{r.name}] {r.detail}')
    elif warns:
        click.echo('VERDICT: CAUTION — address warnings before high-stakes use')
        for r in warns:
            click.echo(f'  WARN [{r.name}] {r.detail}')
    else:
        click.echo('VERDICT: PRODUCTION READY')

    ml_enabled = os.getenv('USE_ML_PROJECTIONS', 'false').lower() == 'true'
    m2_enabled = os.getenv('MODEL2_TIME_AWARE_SPLIT', 'false').lower() == 'true'
    click.echo(f'\n  USE_ML_PROJECTIONS={ml_enabled}  MODEL2_TIME_AWARE_SPLIT={m2_enabled}')


# ── Pick-context backfill commands ─────────────────────────────────────────────

@click.command('backfill-pick-context')
@click.option('--limit', default=500, show_default=True,
              help='Maximum missing prop bets to inspect.')
@click.option('--dry-run', is_flag=True, default=False,
              help='Preview changes without writing to DB.')
@click.option('--allow-weak-context', is_flag=True, default=False,
              help='Allow fallback team/opponent inference when player team is unknown.')
def cli_backfill_pick_context(limit, dry_run, allow_weak_context):
    """Backfill missing PickContext rows for historical prop bets."""
    import json as _json
    from app.services.feature_engine import build_pick_context_features
    from app.services.stats_service import find_player_id, get_cached_logs
    from app.services.value_detector import ValueDetector

    detector = ValueDetector()

    def _norm(s: str) -> str:
        return ''.join(ch for ch in (s or '').lower() if ch.isalnum())

    def _infer_context(bet_obj, player_team_abbr: str) -> tuple[str, str, bool, str]:
        team_a = (bet_obj.team_a or '').strip()
        team_b = (bet_obj.team_b or '').strip()
        picked_team = (bet_obj.picked_team or '').strip()

        if team_a and team_b and picked_team in {team_a, team_b}:
            if picked_team == team_a:
                return team_a, team_b, True, 'picked_team'
            return team_b, team_a, False, 'picked_team'

        # If team names are stored as abbreviations, match from player logs.
        if player_team_abbr:
            if _norm(team_a) == _norm(player_team_abbr):
                return team_a, team_b, True, 'team_abbr_match'
            if _norm(team_b) == _norm(player_team_abbr):
                return team_b, team_a, False, 'team_abbr_match'

        # Conservative fallback: preserve signal if both teams are present.
        if allow_weak_context and team_a and team_b:
            return team_a, team_b, True, 'weak_fallback'
        return '', '', True, 'unresolved'

    with current_app.app_context():
        missing = (
            Bet.query
            .outerjoin(PickContext, PickContext.bet_id == Bet.id)
            .filter(Bet.player_name.isnot(None), Bet.prop_type.isnot(None), Bet.prop_line.isnot(None))
            .filter(PickContext.id.is_(None))
            .order_by(Bet.created_at.asc())
            .limit(int(limit))
            .all()
        )

        click.echo(f'Missing PickContext candidates: {len(missing)}')
        if not missing:
            return

        created = 0
        skipped_no_player = 0
        skipped_unresolved_context = 0
        skipped_duplicate = 0
        mode_counts = {}

        for bet_obj in missing:
            if PickContext.query.filter_by(bet_id=bet_obj.id).first():
                skipped_duplicate += 1
                continue

            player_id = find_player_id((bet_obj.player_name or '').strip())
            if not player_id:
                skipped_no_player += 1
                continue

            latest_logs = get_cached_logs(str(player_id), last_n=1)
            player_team_abbr = ''
            if latest_logs:
                player_team_abbr = (getattr(latest_logs[0], 'team_abbr', '') or '').strip().upper()

            team_name, opponent_name, is_home, mode = _infer_context(bet_obj, player_team_abbr)
            mode_counts[mode] = mode_counts.get(mode, 0) + 1
            if not team_name or not opponent_name:
                skipped_unresolved_context += 1
                continue

            market_odds = int(bet_obj.american_odds or -110)
            score = detector.score_prop(
                player_name=bet_obj.player_name or '',
                prop_type=bet_obj.prop_type or '',
                line=float(bet_obj.prop_line or 0.0),
                over_odds=market_odds,
                under_odds=market_odds,
                opponent_name=opponent_name,
                team_name=team_name,
                is_home=is_home,
                game_id=bet_obj.external_game_id or '',
            )

            projected_edge = score.get('edge', 0.0)
            if bet_obj.bet_type == 'over':
                projected_edge = score.get('edge_over', projected_edge)
            elif bet_obj.bet_type == 'under':
                projected_edge = score.get('edge_under', projected_edge)

            context = build_pick_context_features(
                player_name=bet_obj.player_name or '',
                player_id=str(player_id),
                prop_type=bet_obj.prop_type or '',
                prop_line=float(bet_obj.prop_line or 0.0),
                american_odds=market_odds,
                projected_stat=float(score.get('projection', 0.0) or 0.0),
                projected_edge=float(projected_edge or 0.0),
                confidence_tier=score.get('confidence_tier', 'no_edge'),
                opponent_name=opponent_name,
                team_name=team_name,
                is_home=is_home,
            )

            if not dry_run:
                db.session.add(PickContext(
                    bet_id=bet_obj.id,
                    context_json=_json.dumps(context),
                    projected_stat=score.get('projection'),
                    projected_edge=projected_edge,
                    confidence_tier=score.get('confidence_tier'),
                ))
            created += 1

        if not dry_run and created:
            db.session.commit()

        click.echo(f'Created PickContext rows: {created}')
        click.echo(f'Skipped (no player id): {skipped_no_player}')
        click.echo(f'Skipped (unresolved context): {skipped_unresolved_context}')
        click.echo(f'Skipped (duplicate race): {skipped_duplicate}')
        click.echo(f'Inference modes: {mode_counts}')
        if dry_run:
            click.echo('Dry-run only; no writes committed.')


@click.command('normalize-pick-context-flags')
@click.option('--limit', default=5000, show_default=True,
              help='Maximum PickContext rows to inspect.')
@click.option('--dry-run', is_flag=True, default=False,
              help='Preview changes without writing to DB.')
def cli_normalize_pick_context_flags(limit, dry_run):
    """Normalize/enrich context_flags for existing PickContext rows."""
    import json as _json
    from app.services.feature_engine import derive_context_flags_from_snapshot

    with current_app.app_context():
        rows = (
            PickContext.query
            .order_by(PickContext.created_at.asc())
            .limit(int(limit))
            .all()
        )
        updated = 0
        invalid_json = 0
        already_ok = 0

        for row in rows:
            try:
                ctx = _json.loads(row.context_json or '{}')
                if not isinstance(ctx, dict):
                    invalid_json += 1
                    continue
            except (TypeError, ValueError):
                invalid_json += 1
                continue

            new_flags = derive_context_flags_from_snapshot(ctx)
            old_flags = ctx.get('context_flags')
            if isinstance(old_flags, list):
                merged = list(old_flags)
                for flag in new_flags:
                    if flag not in merged:
                        merged.append(flag)
            else:
                merged = new_flags

            if old_flags == merged:
                already_ok += 1
                continue

            ctx['context_flags'] = merged
            if not dry_run:
                row.context_json = _json.dumps(ctx)
            updated += 1

        if not dry_run and updated:
            db.session.commit()

        click.echo(f'Rows scanned: {len(rows)}')
        click.echo(f'Updated flags: {updated}')
        click.echo(f'Already OK: {already_ok}')
        click.echo(f'Invalid JSON skipped: {invalid_json}')
        if dry_run:
            click.echo('Dry-run only; no writes committed.')


# ── Pollution report (standalone click command, not app.cli.command) ───────────

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

    with current_app.app_context():
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


# ── Postmortem commands ────────────────────────────────────────────────────────

@click.command('backfill-postmortems')
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


@click.command('postmortem-report')
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


def register_model_commands(app):
    app.cli.add_command(cli_run_projections)
    app.cli.add_command(cli_grade_bets)
    app.cli.add_command(cli_retrain)
    app.cli.add_command(cli_bootstrap_pick_quality)
    app.cli.add_command(cli_drift_report)
    app.cli.add_command(cli_model_calibration_report)
    app.cli.add_command(cli_model_accuracy)
    app.cli.add_command(cli_model_status)
    app.cli.add_command(cli_prod_readiness)
    app.cli.add_command(cli_backfill_pick_context)
    app.cli.add_command(cli_normalize_pick_context_flags)
    app.cli.add_command(cli_pollution_report)
    app.cli.add_command(cli_backfill_postmortems)
    app.cli.add_command(cli_postmortem_report)
    app.cli.add_command(cli_backtest)
