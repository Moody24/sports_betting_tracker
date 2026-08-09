"""Rolling-origin real-line backtests for NBA player props."""

from __future__ import annotations

import json
import os
import random
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone

from app import db
from app.models import ModelEvaluationRun, OddsSnapshot
from app.services.distribution import prob_over, prob_over_poisson, rectify_quantiles
from app.services.distribution_calibration import (
    apply_calibrator,
    collect_oof_pairs_poisson,
    collect_oof_pairs_quantile,
    fit_isotonic_calibrator,
)
from app.services.distributional_model import (
    DIST_STAT_TYPES,
    POISSON_DIST_STAT_TYPES,
    QUANTILE_ALPHAS,
    _build_dist_training_rows,
    replay_running_baseline,
)
from app.services.model_diagnostics import begin_evaluation, fail_evaluation, finish_evaluation
from app.services.pick_quality_model import compute_calibration_metrics
from app.utils.odds import american_to_decimal, implied_prob


EDGE_THRESHOLDS = (0.0, 0.02, 0.03, 0.05, 0.075, 0.10, 0.15)
NON_POINT_IN_TIME_FEATURES = ('opp_def_rating', 'opp_pace', 'opp_stat_allowed')


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _month_start(value: date) -> date:
    return value.replace(day=1)


def _next_month(value: date) -> date:
    return (value.replace(day=28) + timedelta(days=4)).replace(day=1)


def _folds(start: date, end: date) -> list[tuple[date, date]]:
    folds = []
    current = _month_start(start)
    while current <= end:
        following = _next_month(current)
        folds.append((max(current, start), min(following, end + timedelta(days=1))))
        current = following
    return folds


def _quote_groups(stat_type: str, start: date, end: date) -> tuple[list[dict], dict]:
    rows = (
        OddsSnapshot.query
        .filter(OddsSnapshot.market == stat_type)
        .filter(OddsSnapshot.game_date >= start, OddsSnapshot.game_date <= end)
        .filter(OddsSnapshot.player_id.isnot(None))
        .order_by(OddsSnapshot.event_start_time, OddsSnapshot.snapped_at)
        .all()
    )
    groups = defaultdict(list)
    for row in rows:
        event_start = _aware(row.event_start_time)
        snapped_at = _aware(row.snapped_at)
        if (
            not event_start or not snapped_at or snapped_at >= event_start
            or row.line is None or not row.bookmaker
        ):
            continue
        event_key = row.source_event_id or row.game_id
        if not event_key:
            continue
        groups[(event_key, str(row.player_id), row.bookmaker)].append(row)

    by_opportunity = defaultdict(list)
    for (event_key, player_id, bookmaker), quotes in groups.items():
        event_start = _aware(quotes[0].event_start_time)
        decision = [
            q for q in quotes
            if q.snapshot_kind == 'decision'
            or event_start - timedelta(minutes=90) <= _aware(q.snapped_at)
            <= event_start - timedelta(minutes=55)
        ]
        closing = [
            q for q in quotes
            if q.snapshot_kind == 'close'
            or event_start - timedelta(minutes=15) <= _aware(q.snapped_at) < event_start
        ]
        if not decision:
            continue
        chosen = max(decision, key=lambda q: _aware(q.snapped_at))
        close = max(closing, key=lambda q: _aware(q.snapped_at)) if closing else None
        by_opportunity[(event_key, player_id)].append((chosen, close))

    opportunities = []
    for (event_key, player_id), book_quotes in by_opportunity.items():
        line_counts = Counter(float(decision.line) for decision, _ in book_quotes)
        priority_line = next(
            (
                float(decision.line)
                for preferred in ('fanduel', 'draftkings')
                for decision, _ in book_quotes
                if decision.bookmaker == preferred
            ),
            min(line_counts),
        )
        consensus_line = max(
            line_counts,
            key=lambda value: (line_counts[value], value == priority_line),
        )
        matching = [pair for pair in book_quotes if float(pair[0].line) == consensus_line]
        for side in ('over', 'under'):
            odds_field = f'{side}_odds'
            available = [pair for pair in matching if getattr(pair[0], odds_field) not in (None, 0)]
            if not available:
                continue
            decision, close = max(available, key=lambda pair: getattr(pair[0], odds_field))
            opportunities.append({
                'event_key': event_key,
                'game_id': decision.game_id or event_key,
                'player_id': player_id,
                'player_name': decision.player_name,
                'game_date': decision.game_date,
                'event_start_time': _aware(decision.event_start_time),
                'side': side,
                'bookmaker': decision.bookmaker,
                'line': float(decision.line),
                'odds': int(getattr(decision, odds_field)),
                'entry_over_odds': decision.over_odds,
                'entry_under_odds': decision.under_odds,
                'close_line': float(close.line) if close and close.line is not None else None,
                'close_over_odds': close.over_odds if close else None,
                'close_under_odds': close.under_odds if close else None,
            })
    coverage = {
        'raw_snapshots': len(rows),
        'decision_opportunities': len({(r['event_key'], r['player_id']) for r in opportunities}),
        'quotes': len(opportunities),
        'unique_games': len({r['event_key'] for r in opportunities}),
        'close_quotes': sum(1 for r in opportunities if r['close_line'] is not None),
    }
    return opportunities, coverage


def _split_pretest_rows(rows: list) -> tuple[list, list, list]:
    unique_dates = sorted({row[0] for row in rows if row[0] is not None})
    if len(unique_dates) < 20:
        return [], [], []
    calibration_count = max(1, int(len(unique_dates) * 0.15))
    calibration_start = unique_dates[-calibration_count]
    train = [row for row in rows if row[0] < calibration_start]
    calibration = [row for row in rows if row[0] >= calibration_start]
    early_count = max(1, int(len(train) * 0.10))
    return train[:-early_count], train[-early_count:], calibration


def _fit_fold_model(stat_type: str, rows: list):
    import numpy as np
    from xgboost import XGBRegressor

    fit_rows, early_rows, calibration_rows = _split_pretest_rows(rows)
    if not fit_rows or not early_rows or not calibration_rows:
        raise ValueError('insufficient pre-test rows for fit/early/calibration partitions')
    feature_names = list(rows[0][2])

    def matrix(selected):
        return np.array([[row[2].get(key, 0.0) for key in feature_names] for row in selected])

    X_fit, X_early, X_cal = matrix(fit_rows), matrix(early_rows), matrix(calibration_rows)
    y_fit = np.array([row[3] for row in fit_rows])
    y_early = np.array([row[3] for row in early_rows])
    y_cal = np.array([row[3] for row in calibration_rows])
    common = dict(
        n_estimators=300, max_depth=5, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.8, reg_alpha=0.1, reg_lambda=1.0,
        random_state=42, n_jobs=1, early_stopping_rounds=25,
    )
    if stat_type in DIST_STAT_TYPES:
        model = XGBRegressor(
            objective='reg:quantileerror', quantile_alpha=QUANTILE_ALPHAS, **common,
        )
        model.fit(X_fit, y_fit, eval_set=[(X_early, y_early)], verbose=False)
        predicted = [rectify_quantiles(values.tolist()) for values in model.predict(X_cal)]
        pairs = collect_oof_pairs_quantile([
            (QUANTILE_ALPHAS, values, float(target))
            for values, target in zip(predicted, y_cal)
        ])
        kind = 'quantile'
    else:
        model = XGBRegressor(objective='count:poisson', **common)
        model.fit(X_fit, y_fit, eval_set=[(X_early, y_early)], verbose=False)
        lambdas = [max(float(value), 0.0) for value in model.predict(X_cal)]
        pairs = collect_oof_pairs_poisson(list(zip(lambdas, y_cal)))
        kind = 'poisson'
    calibrator = fit_isotonic_calibrator(pairs)
    return model, calibrator, feature_names, kind, {
        'fit': len(fit_rows), 'early': len(early_rows), 'calibration': len(calibration_rows),
    }


def _point_in_time_rows(rows: list) -> list:
    """Remove current-only defense snapshots from historical evaluation."""
    cleaned = []
    for game_date, player_id, features, target in rows:
        point_in_time = dict(features)
        for key in NON_POINT_IN_TIME_FEATURES:
            point_in_time[key] = 0.0
        cleaned.append((game_date, player_id, point_in_time, target))
    return cleaned


def _model_probability(model_bundle, features: dict, line: float) -> float | None:
    import numpy as np

    model, calibrator, feature_names, kind, _ = model_bundle
    X = np.array([[features.get(key, 0.0) for key in feature_names]])
    if kind == 'quantile':
        values = rectify_quantiles(model.predict(X)[0].tolist())
        if line < values[0] or line > values[-1]:
            return None
        raw = prob_over(line, QUANTILE_ALPHAS, values)
    else:
        raw = prob_over_poisson(line, max(float(model.predict(X)[0]), 0.0))
    return apply_calibrator(calibrator, raw)


def _outcome(target: float, line: float, side: str) -> str:
    if target == line:
        return 'push'
    over_won = target > line
    return 'win' if over_won == (side == 'over') else 'lose'


def _no_vig_side_probability(over_odds, under_odds, side: str) -> float | None:
    if over_odds in (None, 0) or under_odds in (None, 0):
        return None
    over = implied_prob(int(over_odds))
    under = implied_prob(int(under_odds))
    denominator = over + under
    return (over if side == 'over' else under) / denominator if denominator else None


def _max_drawdown(values: list[float]) -> float:
    peak = values[0] if values else 0.0
    maximum = 0.0
    for value in values:
        peak = max(peak, value)
        maximum = max(maximum, peak - value)
    return maximum


def _roi(rows: list[dict]) -> float:
    stake = sum(float(row.get('stake', 1.0)) for row in rows)
    profit = sum(float(row['profit']) for row in rows)
    return profit / stake if stake else 0.0


def _bootstrap_roi(rows: list[dict], iterations: int = 500) -> list[float]:
    grouped = defaultdict(list)
    for row in rows:
        grouped[row['game_id']].append(row)
    games = sorted(grouped)
    if not games:
        return [0.0, 0.0]
    rng = random.Random(42)
    values = []
    for _ in range(iterations):
        sample = []
        for game in rng.choices(games, k=len(games)):
            sample.extend(grouped[game])
        values.append(_roi(sample))
    values.sort()
    return [round(values[int(iterations * 0.025)], 4), round(values[int(iterations * 0.975)], 4)]


def _kelly_simulation(rows: list[dict]) -> dict:
    bankroll = 100.0
    peak = bankroll
    maximum_drawdown_pct = 0.0
    by_event = defaultdict(list)
    for row in rows:
        by_event[(row['event_start_time'], row['game_id'])].append(row)
    for key in sorted(by_event):
        event_rows = by_event[key]
        proposed = []
        for row in event_rows:
            decimal = american_to_decimal(row['odds'])
            b = decimal - 1.0
            p = row['probability']
            full = max((b * p - (1 - p)) / b, 0.0) if b > 0 else 0.0
            proposed.append(min(bankroll * full * 0.25, bankroll * 0.05))
        total = sum(proposed)
        scale = min(1.0, (bankroll * 0.10) / total) if total else 0.0
        event_profit = 0.0
        for row, raw_stake in zip(event_rows, proposed):
            stake = raw_stake * scale
            if row['outcome'] == 'win':
                event_profit += stake * (american_to_decimal(row['odds']) - 1.0)
            elif row['outcome'] == 'lose':
                event_profit -= stake
        bankroll += event_profit
        peak = max(peak, bankroll)
        if peak:
            maximum_drawdown_pct = max(maximum_drawdown_pct, (peak - bankroll) / peak)
    return {
        'ending_bankroll': round(bankroll, 4),
        'return': round(bankroll / 100.0 - 1.0, 4),
        'max_drawdown_pct': round(maximum_drawdown_pct, 4),
    }


def summarize_bets(rows: list[dict]) -> dict:
    settled = [row for row in rows if row['outcome'] != 'push']
    flat = []
    bankroll_path = [0.0]
    cumulative = 0.0
    for row in sorted(rows, key=lambda value: (value['event_start_time'], value['game_id'])):
        if row['outcome'] == 'win':
            profit = american_to_decimal(row['odds']) - 1.0
        elif row['outcome'] == 'lose':
            profit = -1.0
        else:
            profit = 0.0
        copied = {**row, 'profit': profit, 'stake': 1.0}
        flat.append(copied)
        cumulative += profit
        bankroll_path.append(cumulative)
    calibration = compute_calibration_metrics([
        (row['probability'], 1 if row['outcome'] == 'win' else 0) for row in settled
    ]) if settled else {'ece': 0.0, 'brier': 0.0, 'logloss': 0.0}
    line_clv = [
        (row['close_line'] - row['line']) if row['side'] == 'over'
        else (row['line'] - row['close_line'])
        for row in rows if row['close_line'] is not None
    ]
    price_clv = []
    for row in rows:
        if row['close_line'] != row['line']:
            continue
        entry_probability = _no_vig_side_probability(
            row['entry_over_odds'], row['entry_under_odds'], row['side'],
        )
        close_probability = _no_vig_side_probability(
            row['close_over_odds'], row['close_under_odds'], row['side'],
        )
        if close_probability is not None and entry_probability is not None:
            price_clv.append(close_probability - entry_probability)
    return {
        'bets': len(rows),
        'unique_games': len({row['game_id'] for row in rows}),
        'wins': sum(row['outcome'] == 'win' for row in rows),
        'losses': sum(row['outcome'] == 'lose' for row in rows),
        'pushes': sum(row['outcome'] == 'push' for row in rows),
        'hit_rate': round(
            sum(row['outcome'] == 'win' for row in rows) / len(settled), 4,
        ) if settled else 0.0,
        'flat_profit_units': round(sum(row['profit'] for row in flat), 4),
        'flat_roi': round(_roi(flat), 4),
        'flat_roi_ci95': _bootstrap_roi(flat),
        'max_drawdown_units': round(_max_drawdown(bankroll_path), 4),
        'mean_line_clv': round(sum(line_clv) / len(line_clv), 4) if line_clv else None,
        'line_clv_n': len(line_clv),
        'mean_price_clv': round(sum(price_clv) / len(price_clv), 4) if price_clv else None,
        'price_clv_n': len(price_clv),
        'kelly': _kelly_simulation(rows),
        **{key: calibration[key] for key in ('ece', 'brier', 'logloss')},
    }


def summarize_segments(rows: list[dict]) -> dict:
    """Compact fold/side/bookmaker breakdown without repeated bootstraps."""
    output = {}
    for dimension in ('fold', 'side', 'bookmaker'):
        grouped = defaultdict(list)
        for row in rows:
            grouped[str(row.get(dimension) or 'unknown')].append(row)
        output[dimension] = {}
        for value, members in sorted(grouped.items()):
            settled = [row for row in members if row['outcome'] != 'push']
            profits = []
            for row in members:
                if row['outcome'] == 'win':
                    profits.append(american_to_decimal(row['odds']) - 1.0)
                elif row['outcome'] == 'lose':
                    profits.append(-1.0)
                else:
                    profits.append(0.0)
            calibration = compute_calibration_metrics([
                (row['probability'], int(row['outcome'] == 'win')) for row in settled
            ]) if settled else {'ece': 0.0, 'brier': 0.0, 'logloss': 0.0}
            clv = [
                (row['close_line'] - row['line']) if row['side'] == 'over'
                else (row['line'] - row['close_line'])
                for row in members if row['close_line'] is not None
            ]
            output[dimension][value] = {
                'bets': len(members),
                'wins': sum(row['outcome'] == 'win' for row in members),
                'losses': sum(row['outcome'] == 'lose' for row in members),
                'pushes': sum(row['outcome'] == 'push' for row in members),
                'hit_rate': round(
                    sum(row['outcome'] == 'win' for row in members) / len(settled), 4,
                ) if settled else 0.0,
                'flat_roi': round(sum(profits) / len(members), 4) if members else 0.0,
                'mean_line_clv': round(sum(clv) / len(clv), 4) if clv else None,
                **{key: calibration[key] for key in ('ece', 'brier', 'logloss')},
            }
    return output


def _select_bets(scored: list[dict], model_key: str, threshold: float) -> list[dict]:
    grouped = defaultdict(list)
    for row in scored:
        probability = row[f'{model_key}_probability']
        side_probability = probability if row['side'] == 'over' else 1.0 - probability
        edge = side_probability - implied_prob(row['odds'])
        grouped[(row['event_key'], row['player_id'])].append((edge, side_probability, row))
    selected = []
    for candidates in grouped.values():
        edge, probability, row = max(candidates, key=lambda value: value[0])
        if edge < threshold:
            continue
        selected.append({
            **row,
            'probability': probability,
            'edge': edge,
            'outcome': _outcome(row['target'], row['line'], row['side']),
        })
    return selected


def promotion_verdict(challenger: dict, incumbent: dict, coverage: dict) -> str:
    close_coverage = challenger['line_clv_n'] / challenger['bets'] if challenger['bets'] else 0.0
    decision_join = coverage.get('settled_joined', 0) / max(coverage.get('decision_opportunities', 0), 1)
    eligible = (
        challenger['bets'] >= 1000
        and challenger['unique_games'] >= 200
        and coverage.get('folds_scored', 0) >= 3
        and decision_join >= 0.80
        and close_coverage >= 0.60
    )
    if not eligible:
        return 'SHADOW'
    passed = (
        challenger['ece'] <= 0.03
        and challenger['ece'] <= incumbent['ece']
        and challenger['brier'] <= incumbent['brier'] * 1.01
        and challenger['logloss'] <= incumbent['logloss'] * 1.01
        and (challenger['mean_line_clv'] or 0.0) > 0.0
        and challenger['flat_roi'] > incumbent['flat_roi']
        and challenger['flat_roi_ci95'][0] >= -0.02
        and challenger['kelly']['max_drawdown_pct'] <= 0.20
    )
    return 'PROMOTE_CANDIDATE' if passed else 'HOLD'


def _consecutive_verdict(stat_type: str, verdict: str) -> str:
    if verdict != 'PROMOTE_CANDIDATE':
        return verdict
    previous = (
        ModelEvaluationRun.query
        .filter_by(
            evaluation_type='rolling_real_line_backtest',
            stat_type=stat_type,
            status='success',
        )
        .order_by(ModelEvaluationRun.finished_at.desc())
        .first()
    )
    return 'PROMOTE' if previous and previous.verdict == 'PROMOTE_CANDIDATE' else verdict


def run_rolling_backtest(
    stat_type: str,
    date_from: date,
    date_to: date,
    selected_threshold: float = 0.03,
) -> dict:
    if stat_type not in DIST_STAT_TYPES + POISSON_DIST_STAT_TYPES:
        return {'error': f'Unsupported stat type: {stat_type}'}
    run = begin_evaluation('rolling_real_line_backtest', stat_type, {
        'date_from': date_from.isoformat(),
        'date_to': date_to.isoformat(),
        'edge_thresholds': EDGE_THRESHOLDS,
        'selected_threshold': selected_threshold,
        'decision_time': 'T-60',
        'close_time': 'T-10',
        'fold_method': 'expanding_monthly',
        'minimum_history_days': 365,
        'non_point_in_time_features_zeroed': NON_POINT_IN_TIME_FEATURES,
        'xgboost': {
            'n_estimators': 300, 'max_depth': 5, 'learning_rate': 0.05,
            'subsample': 0.8, 'colsample_bytree': 0.8,
            'reg_alpha': 0.1, 'reg_lambda': 1.0,
            'early_stopping_rounds': 25, 'random_state': 42,
        },
    }, model_name=f'dist_{stat_type}',
       model_version=f'rolling_{date_from.isoformat()}_{date_to.isoformat()}')
    try:
        opportunities, coverage = _quote_groups(stat_type, date_from, date_to)
        if not opportunities:
            raise ValueError('No resolvable real-line decision quotes in the requested range')
        rows = _build_dist_training_rows(stat_type) if stat_type in DIST_STAT_TYPES else None
        if rows is None:
            from app.services.ml_model import _build_training_rows
            rows = _build_training_rows(stat_type, min_train_samples=0)
        if not rows:
            raise ValueError('No historical training rows available')
        rows = _point_in_time_rows(rows)
        row_lookup = {(row[0], str(row[1])): row for row in rows}
        scored = []
        baseline_cache = {}
        fold_reports = []
        for fold_start, fold_end in _folds(date_from, date_to):
            fold_quotes = [
                quote for quote in opportunities if fold_start <= quote['game_date'] < fold_end
            ]
            if not fold_quotes:
                continue
            pretest = [row for row in rows if row[0] and row[0] < fold_start]
            if not pretest or (fold_start - pretest[0][0]).days < 365:
                fold_reports.append({
                    'start': fold_start.isoformat(), 'end': fold_end.isoformat(),
                    'status': 'skipped', 'reason': 'less_than_365_days_training_history',
                })
                continue
            bundle = _fit_fold_model(stat_type, pretest)
            joined = 0
            for quote in fold_quotes:
                training_row = row_lookup.get((quote['game_date'], quote['player_id']))
                if training_row is None:
                    continue
                baseline_key = (training_row[0], str(training_row[1]))
                if baseline_key not in baseline_cache:
                    baseline_cache[baseline_key] = replay_running_baseline(
                        training_row, stat_type,
                    )
                baseline = baseline_cache[baseline_key]
                if baseline is None:
                    continue
                from scipy.stats import norm
                baseline_over = float(1.0 - norm.cdf(
                    quote['line'], loc=baseline[0], scale=baseline[1],
                ))
                challenger_over = _model_probability(bundle, training_row[2], quote['line'])
                if challenger_over is None:
                    challenger_over = baseline_over
                scored.append({
                    **quote,
                    'target': float(training_row[3]),
                    'challenger_probability': challenger_over,
                    'incumbent_probability': baseline_over,
                    'fold': fold_start.isoformat(),
                })
                joined += 1
            fold_reports.append({
                'start': fold_start.isoformat(), 'end': fold_end.isoformat(),
                'status': 'scored', 'joined_quotes': joined,
                'training': bundle[4],
            })

        coverage['settled_joined'] = len({
            (row['event_key'], row['player_id']) for row in scored
        })
        coverage['folds_scored'] = sum(
            row['status'] == 'scored' and row.get('joined_quotes', 0) > 0
            for row in fold_reports
        )
        if not scored:
            raise ValueError('No quotes joined to leakage-safe historical feature rows')
        thresholds = {}
        for threshold in EDGE_THRESHOLDS:
            challenger_bets = _select_bets(scored, 'challenger', threshold)
            incumbent_bets = _select_bets(scored, 'incumbent', threshold)
            thresholds[str(threshold)] = {
                'challenger': {
                    **summarize_bets(challenger_bets),
                    'segments': summarize_segments(challenger_bets),
                },
                'incumbent': {
                    **summarize_bets(incumbent_bets),
                    'segments': summarize_segments(incumbent_bets),
                },
            }
        selected_key = str(float(selected_threshold))
        if selected_key not in thresholds:
            challenger_bets = _select_bets(scored, 'challenger', selected_threshold)
            incumbent_bets = _select_bets(scored, 'incumbent', selected_threshold)
            thresholds[selected_key] = {
                'challenger': {
                    **summarize_bets(challenger_bets),
                    'segments': summarize_segments(challenger_bets),
                },
                'incumbent': {
                    **summarize_bets(incumbent_bets),
                    'segments': summarize_segments(incumbent_bets),
                },
            }
        selected = thresholds[selected_key]
        verdict = _consecutive_verdict(
            stat_type,
            promotion_verdict(selected['challenger'], selected['incumbent'], coverage),
        )
        report = {
            'stat_type': stat_type,
            'coverage': coverage,
            'folds': fold_reports,
            'thresholds': thresholds,
            'selected_threshold': selected_threshold,
            'verdict': verdict,
        }
        artifact_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'ml_models', 'evaluations')
        os.makedirs(artifact_dir, exist_ok=True)
        artifact_path = os.path.join(
            artifact_dir,
            f'rolling_{stat_type}_{date_from.isoformat()}_{date_to.isoformat()}_{run.id}.json',
        )
        with open(artifact_path, 'w', encoding='utf-8') as handle:
            json.dump(report, handle, sort_keys=True, indent=2, default=str)
        finish_evaluation(run, report, verdict=verdict, artifact_path=artifact_path)
        return report
    except Exception as exc:
        db.session.rollback()
        run = db.session.get(ModelEvaluationRun, run.id)
        fail_evaluation(run, str(exc))
        return {'error': str(exc), 'stat_type': stat_type}
