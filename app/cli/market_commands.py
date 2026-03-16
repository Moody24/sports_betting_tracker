"""Market model Flask CLI commands."""

import logging
from datetime import datetime, timedelta

import click

from app.models import GameSnapshot
from app.cli import APP_TIMEZONE

logger = logging.getLogger(__name__)


@click.command('train-market-models')
@click.option('--min-samples', type=int, default=60, show_default=True)
def cli_train_market_models(min_samples):
    from app.services.market_recommender import train_market_models
    click.echo('Training market models (moneyline + total O/U)...')
    result = train_market_models(min_samples=min_samples)
    click.echo(f'Market model train result: {result}')


@click.command('market-model-report')
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


@click.command('market-threshold-tune')
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


@click.command('market-guard-check')
@click.option('--days', type=int, default=60, show_default=True, help='Evaluation window in days.')
@click.option('--bins', type=int, default=5, show_default=True, help='Calibration bins.')
@click.option('--drift-threshold', type=float, default=0.05, show_default=True, help='Abs accuracy delta threshold.')
@click.option('--min-bets', type=int, default=20, show_default=True, help='Minimum bets before ROI disable gate applies.')
@click.option('--apply/--no-apply', default=True, show_default=True, help='Persist enable/disable decisions.')
def cli_market_guard_check(days, bins, drift_threshold, min_bets, apply):
    """Auto-disable weak markets based on drift and ROI guardrails."""
    from app.services.market_recommender import guard_market_recommendations

    click.echo(f'=== Market Guard Check (last {days} days) ===')
    result = guard_market_recommendations(
        days=days,
        bins=bins,
        drift_threshold=drift_threshold,
        min_bets=min_bets,
        apply=apply,
    )
    if result.get('error'):
        click.echo(f"Error: {result['error']}")
        click.echo(f"Rows scanned: {result.get('rows_scanned', 0)}")
        return
    for market in ('moneyline', 'total_ou'):
        d = (result.get('decisions') or {}).get(market, {})
        click.echo(f"\n--- {market} ---")
        click.echo(
            f"Decision={d.get('decision')} | drift_breach={d.get('drift_breach')} | "
            f"roi_breach={d.get('roi_breach')} | wf_roi_breach={d.get('walkforward_roi_breach')} | "
            f"bets={d.get('recommended_bets')} | "
            f"acc_delta={d.get('accuracy_delta')} | roi/bet={d.get('roi_per_bet')}"
        )
        click.echo(
            f"Walk-forward: folds={d.get('walkforward_folds')} "
            f"avg_roi_per_bet={d.get('walkforward_avg_roi_per_bet')}"
        )
    click.echo('\n=== Apply ===')
    if result.get('applied'):
        click.echo(f"Applied: {result.get('apply_result')}")
    else:
        click.echo('No changes persisted (--no-apply).')


@click.command('market-walkforward-report')
@click.option('--days', type=int, default=180, show_default=True, help='History window in days.')
@click.option('--train-days', type=int, default=60, show_default=True, help='Train window per fold.')
@click.option('--test-days', type=int, default=14, show_default=True, help='Test window per fold.')
@click.option('--step-days', type=int, default=14, show_default=True, help='Fold step size.')
@click.option('--bins', type=int, default=5, show_default=True, help='Calibration bins.')
def cli_market_walkforward_report(days, train_days, test_days, step_days, bins):
    """Walk-forward report for market model stability and re-enable decisions."""
    from app.services.market_recommender import walkforward_market_report

    click.echo(f'=== Market Walk-Forward Report (last {days} days) ===')
    report = walkforward_market_report(
        days=days,
        train_days=train_days,
        test_days=test_days,
        step_days=step_days,
        bins=bins,
    )
    if report.get('error'):
        click.echo(f"Error: {report['error']}")
        click.echo(f"Rows scanned: {report.get('rows_scanned', 0)}")
        return
    click.echo(f"Rows scanned: {report.get('rows_scanned')}")
    click.echo(f"Policy used: {report.get('policy_used')}")
    for market in ('moneyline', 'total_ou'):
        m = (report.get('markets') or {}).get(market, {})
        click.echo(f"\n--- {market} ---")
        click.echo(f"Summary: {m.get('summary')}")
        folds = m.get('folds') or []
        for f in folds[-5:]:
            click.echo(
                f"Fold {f.get('test_start')}..{f.get('test_end')} | rows={f.get('rows')} | "
                f"acc={f.get('accuracy')} | brier={f.get('brier')} | "
                f"bets={f.get('recommended_bets')} | roi/bet={f.get('roi_per_bet')}"
            )


@click.command('market-governance-run')
@click.option('--days', type=int, default=180, show_default=True)
@click.option('--bins', type=int, default=5, show_default=True)
@click.option('--min-bets', type=int, default=20, show_default=True)
@click.option('--drift-threshold', type=float, default=0.05, show_default=True)
@click.option('--train-days', type=int, default=60, show_default=True)
@click.option('--test-days', type=int, default=14, show_default=True)
@click.option('--step-days', type=int, default=14, show_default=True)
@click.option('--apply/--no-apply', default=True, show_default=True)
def cli_market_governance_run(days, bins, min_bets, drift_threshold, train_days, test_days, step_days, apply):
    """Run full market governance cycle (tune + guard + walk-forward)."""
    from app.services.market_recommender import run_market_governance

    click.echo(f'=== Market Governance Run (last {days} days) ===')
    result = run_market_governance(
        days=days,
        bins=bins,
        min_bets=min_bets,
        drift_threshold=drift_threshold,
        train_days=train_days,
        test_days=test_days,
        step_days=step_days,
        apply=apply,
    )
    click.echo(f"Tune summary: {(result.get('tune') or {}).get('selected')}")
    click.echo(f"Guard summary: {(result.get('guard') or {}).get('decisions')}")
    wf = (result.get('walkforward') or {}).get('markets', {})
    click.echo(
        "Walk-forward summary: "
        f"moneyline={(wf.get('moneyline') or {}).get('summary')} | "
        f"total_ou={(wf.get('total_ou') or {}).get('summary')}"
    )


@click.command('market-data-coverage-report')
@click.option('--days', type=int, default=180, show_default=True)
@click.option('--train-days', type=int, default=60, show_default=True)
@click.option('--test-days', type=int, default=14, show_default=True)
@click.option('--step-days', type=int, default=14, show_default=True)
def cli_market_data_coverage_report(days, train_days, test_days, step_days):
    """Report market-snapshot coverage and walk-forward fold feasibility."""
    from app.services.market_recommender import walkforward_market_report

    cutoff = datetime.now(APP_TIMEZONE).date() - timedelta(days=days)
    rows = (
        GameSnapshot.query
        .filter(GameSnapshot.game_date >= cutoff)
        .filter(GameSnapshot.is_final.is_(True))
        .filter(GameSnapshot.home_score.isnot(None))
        .filter(GameSnapshot.away_score.isnot(None))
        .filter(GameSnapshot.over_under_line.isnot(None))
        .filter(GameSnapshot.moneyline_home.isnot(None))
        .filter(GameSnapshot.moneyline_away.isnot(None))
        .all()
    )
    dates = sorted({r.game_date for r in rows if r.game_date is not None})
    click.echo(f'=== Market Data Coverage (last {days} days) ===')
    click.echo(f'Usable rows: {len(rows)}')
    click.echo(f'Unique dates: {len(dates)}')
    if dates:
        click.echo(f'Date range: {dates[0]} -> {dates[-1]}')

    wf = walkforward_market_report(
        days=days,
        train_days=train_days,
        test_days=test_days,
        step_days=step_days,
        bins=5,
    )
    if wf.get('error'):
        click.echo(f"Walk-forward feasibility: NOT READY ({wf.get('error')})")
    else:
        m = (wf.get('markets') or {}).get('moneyline', {}).get('summary', {})
        t = (wf.get('markets') or {}).get('total_ou', {}).get('summary', {})
        click.echo('Walk-forward feasibility: READY')
        click.echo(f"Moneyline summary: {m}")
        click.echo(f"Total O/U summary: {t}")


@click.command('ingest-historical-market-odds')
@click.option('--start-date', required=True, help='Start date (YYYY-MM-DD)')
@click.option('--end-date', required=True, help='End date (YYYY-MM-DD)')
@click.option('--force/--no-force', default=False, show_default=True)
@click.option('--sleep', 'sleep_seconds', type=float, default=0.1, show_default=True)
def cli_ingest_historical_market_odds(start_date, end_date, force, sleep_seconds):
    """Ingest historical moneyline + totals into GameSnapshot from odds providers."""
    from app.services.nba_service import ingest_historical_market_odds

    try:
        start_dt = datetime.strptime(start_date, '%Y-%m-%d').date()
        end_dt = datetime.strptime(end_date, '%Y-%m-%d').date()
    except ValueError:
        click.echo('Invalid date format. Use YYYY-MM-DD.')
        return

    click.echo(f'Ingesting historical market odds: {start_dt} -> {end_dt}')
    result = ingest_historical_market_odds(
        start_date=start_dt,
        end_date=end_dt,
        force=force,
        sleep_seconds=sleep_seconds,
    )
    if result.get('error'):
        click.echo(f"Error: {result['error']}")
        return
    click.echo(
        f"Ingest result: scanned_days={result.get('scanned_days')} "
        f"odds_games={result.get('odds_games')} matched_snapshots={result.get('matched_snapshots')} "
        f"ou_updated={result.get('ou_updated')} moneyline_updated={result.get('moneyline_updated')} "
        f"fallback_days={result.get('fallback_days')} errors={result.get('errors')}"
    )


def register_market_commands(app):
    app.cli.add_command(cli_train_market_models)
    app.cli.add_command(cli_market_model_report)
    app.cli.add_command(cli_market_threshold_tune)
    app.cli.add_command(cli_market_guard_check)
    app.cli.add_command(cli_market_walkforward_report)
    app.cli.add_command(cli_market_governance_run)
    app.cli.add_command(cli_market_data_coverage_report)
    app.cli.add_command(cli_ingest_historical_market_odds)
