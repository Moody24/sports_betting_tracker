"""Observability CLI commands — projection drift, scheduler health, ML status."""

import click
import statistics
from collections import defaultdict
from datetime import datetime, timezone, timedelta

from app import db
from app.models import BetPostmortem, JobLog, ModelMetadata


def register_observability_commands(app):

    @app.cli.command('health-report')
    @click.option('--days', default=30, show_default=True,
                  help='Rolling window for projection drift analysis.')
    @click.option('--job-days', default=7, show_default=True,
                  help='Rolling window for scheduler job health.')
    def health_report(days, job_days):
        """Projection drift, scheduler health, and ML model status."""
        _print_projection_drift(days)
        _print_scheduler_health(job_days)
        _print_model_status()


def _print_projection_drift(days: int) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (
        db.session.query(
            BetPostmortem.stat_type,
            BetPostmortem.projected_stat,
            BetPostmortem.actual_stat,
            BetPostmortem.projection_error,
        )
        .filter(BetPostmortem.projected_stat.isnot(None))
        .filter(BetPostmortem.actual_stat.isnot(None))
        .filter(BetPostmortem.projection_error.isnot(None))
        .filter(BetPostmortem.created_at >= cutoff)
        .all()
    )

    click.echo(f"\n=== Projection Drift (last {days} days, N={len(rows)}) ===")
    if not rows:
        click.echo("  No postmortem data in window.")
        return

    by_type = defaultdict(list)
    for r in rows:
        by_type[r.stat_type].append(float(r.projection_error))

    click.echo(f"  {'stat_type':38s} {'N':>5} {'avg_err':>9} {'MAE':>7} {'over%':>7}")
    click.echo('  ' + '-' * 72)

    overall_errs = []
    for stat_type, errs in sorted(by_type.items()):
        avg_err = sum(errs) / len(errs)
        mae = sum(abs(e) for e in errs) / len(errs)
        over_pct = sum(1 for e in errs if e > 0) / len(errs) * 100
        flag = ''
        if abs(avg_err) > 2.0:
            flag = ' <-- DRIFT'
        elif abs(avg_err) > 1.0:
            flag = ' <-- WATCH'
        click.echo(
            f"  {stat_type:38s} {len(errs):>5} {avg_err:>+9.2f} {mae:>7.2f} {over_pct:>6.1f}%{flag}"
        )
        overall_errs.extend(errs)

    overall_avg = sum(overall_errs) / len(overall_errs)
    overall_mae = sum(abs(e) for e in overall_errs) / len(overall_errs)
    if len(overall_errs) >= 2:
        overall_std = statistics.stdev(overall_errs)
    else:
        overall_std = 0.0
    click.echo(f"\n  Overall: avg_err={overall_avg:+.2f}  MAE={overall_mae:.2f}  std={overall_std:.2f}")


def _print_scheduler_health(job_days: int) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=job_days)
    rows = (
        db.session.query(
            JobLog.job_name,
            JobLog.status,
            JobLog.started_at,
            JobLog.finished_at,
        )
        .filter(JobLog.started_at >= cutoff)
        .order_by(JobLog.started_at.desc())
        .all()
    )

    click.echo(f"\n=== Scheduler Health (last {job_days} days, {len(rows)} runs) ===")
    if not rows:
        click.echo("  No job logs in window.")
        return

    by_job = defaultdict(list)
    for r in rows:
        by_job[r.job_name].append(r)

    click.echo(f"  {'job_name':35s} {'runs':>5} {'ok':>5} {'fail':>6} {'last_status':>12} {'last_run':>22}")
    click.echo('  ' + '-' * 92)
    for job_name, job_rows in sorted(by_job.items()):
        ok = sum(1 for r in job_rows if r.status == 'success')
        fail = len(job_rows) - ok
        last = job_rows[0]
        last_run_str = last.started_at.strftime('%Y-%m-%d %H:%M ET') if last.started_at else 'unknown'
        warn = sum(1 for r in job_rows if r.status == 'warn')
        hard_fail = fail - warn
        flag = ''
        if hard_fail > 0:
            flag = ' <-- FAILURES'
        elif warn > 0:
            flag = ' <-- WARN'
        click.echo(
            f"  {job_name:35s} {len(job_rows):>5} {ok:>5} {fail:>6} {last.status:>12} {last_run_str:>22}{flag}"
        )
        if flag and last.message:
            click.echo(f"    last msg: {last.message[:120]}")


def _print_model_status() -> None:
    models = (
        ModelMetadata.query
        .filter_by(is_active=True)
        .order_by(ModelMetadata.created_at.desc())
        .all()
    )

    click.echo(f"\n=== Active ML Models ({len(models)}) ===")
    if not models:
        click.echo("  No active models found.")
        return

    click.echo(f"  {'model_name':35s} {'trained':>12} {'val_acc':>9} {'age_days':>10}")
    click.echo('  ' + '-' * 72)
    now = datetime.now(timezone.utc)
    for m in models:
        trained_str = m.created_at.strftime('%Y-%m-%d') if m.created_at else 'unknown'
        val_acc = f"{m.val_accuracy:.3f}" if m.val_accuracy else 'n/a'
        age = (now - m.created_at.replace(tzinfo=timezone.utc)).days if m.created_at else -1
        age_flag = ' <-- STALE' if age > 14 else ''
        click.echo(f"  {m.model_name:35s} {trained_str:>12} {val_acc:>9} {age:>10}d{age_flag}")
    click.echo()
