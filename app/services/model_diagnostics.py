"""Temporal model diagnostics shared by training and CLI evaluation."""

from __future__ import annotations

import json
import logging
import subprocess
from collections import defaultdict
from datetime import datetime, timezone

from app import db
from app.models import ModelEvaluationRun, ModelMetadata
from app.services.ml_feature_builder import extract_opp_abbr

logger = logging.getLogger(__name__)


def _mae(actual, predicted) -> float:
    values = [abs(float(a) - float(p)) for a, p in zip(actual, predicted)]
    return float(sum(values) / len(values)) if values else 0.0


def _bias(actual, predicted) -> float:
    values = [float(p) - float(a) for a, p in zip(actual, predicted)]
    return float(sum(values) / len(values)) if values else 0.0


def _git_revision() -> str | None:
    try:
        return subprocess.run(
            ['git', 'rev-parse', 'HEAD'],
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
        ).stdout.strip()[:40]
    except (OSError, subprocess.SubprocessError):
        return None


def begin_evaluation(
    evaluation_type: str,
    stat_type: str | None,
    config: dict,
    model_name: str | None = None,
    model_version: str | None = None,
) -> ModelEvaluationRun:
    run = ModelEvaluationRun(
        evaluation_type=evaluation_type,
        model_name=model_name,
        model_version=model_version,
        stat_type=stat_type,
        started_at=datetime.now(timezone.utc),
        status='running',
        config_json=json.dumps(config, sort_keys=True, default=str),
        code_revision=_git_revision(),
    )
    db.session.add(run)
    db.session.commit()
    return run


def finish_evaluation(
    run: ModelEvaluationRun,
    metrics: dict,
    verdict: str | None = None,
    status: str = 'success',
    artifact_path: str | None = None,
) -> ModelEvaluationRun:
    run.finished_at = datetime.now(timezone.utc)
    run.status = status
    run.verdict = verdict
    run.metrics_json = json.dumps(metrics, sort_keys=True, default=str)
    run.artifact_path = artifact_path
    db.session.commit()
    return run


def fail_evaluation(run: ModelEvaluationRun, error: str) -> None:
    finish_evaluation(run, {'error': str(error)}, status='failed')


def chronological_split(rows: list, train_fraction: float = 0.8) -> tuple[list[int], list[int], object]:
    """Date-boundary split that never places one game date in both sides."""
    unique_dates = sorted({row[0] for row in rows if row[0] is not None})
    if len(unique_dates) < 2:
        return [], [], None
    cutoff_idx = max(0, min(int(len(unique_dates) * train_fraction) - 1, len(unique_dates) - 2))
    cutoff = unique_dates[cutoff_idx]
    train_idx = [i for i, row in enumerate(rows) if row[0] is not None and row[0] <= cutoff]
    val_idx = [i for i, row in enumerate(rows) if row[0] is not None and row[0] > cutoff]
    return train_idx, val_idx, cutoff


def regression_summary(y_train, pred_train, y_val, pred_val) -> dict:
    """Return under/overfitting diagnostics without stat-specific thresholds."""
    import numpy as np

    train_mae = _mae(y_train, pred_train)
    val_mae = _mae(y_val, pred_val)
    train_std = float(np.std(y_train)) if len(y_train) else 0.0
    val_std = float(np.std(y_val)) if len(y_val) else 0.0
    baseline_value = float(np.mean(y_train)) if len(y_train) else 0.0
    baseline_pred = [baseline_value] * len(y_val)
    baseline_mae = _mae(y_val, baseline_pred)
    improvement = (baseline_mae - val_mae) / baseline_mae if baseline_mae else 0.0
    train_nmae = train_mae / train_std if train_std else 0.0
    val_nmae = val_mae / val_std if val_std else 0.0
    ratio = val_mae / train_mae if train_mae else (999.0 if val_mae else 0.0)
    return {
        'train_mae': round(train_mae, 4),
        'val_mae': round(val_mae, 4),
        'generalization_gap': round(val_mae - train_mae, 4),
        'generalization_ratio': round(ratio, 4),
        'train_normalized_mae': round(train_nmae, 4),
        'val_normalized_mae': round(val_nmae, 4),
        'historical_average_baseline_mae': round(baseline_mae, 4),
        'baseline_improvement': round(improvement, 4),
        'likely_underfit': bool(train_nmae >= 0.8 and val_nmae >= 0.8 and improvement < 0.02),
        'likely_overfit': bool(ratio > 1.25),
    }


def _minutes_band(value: float) -> str:
    if value < 20:
        return '<20'
    if value < 30:
        return '20-29.9'
    if value < 36:
        return '30-35.9'
    return '36+'


def _games_band(value: float) -> str:
    if value < 20:
        return '<20'
    if value < 50:
        return '20-49'
    return '50+'


def _season(game_date) -> str:
    if game_date is None:
        return 'unknown'
    start = game_date.year if game_date.month >= 10 else game_date.year - 1
    return f'{start}-{str(start + 1)[-2:]}'


def _row_context_lookup() -> dict:
    from app.services.historical_training_source import load_historical_training_logs

    lookup = {}
    for row in load_historical_training_logs():
        lookup[(str(row.player_id), row.game_date)] = {
            'player': row.player_name or str(row.player_id),
            'opponent': extract_opp_abbr(row.matchup or '') or 'unknown',
            'minutes': float(row.minutes or 0.0),
        }
    return lookup


def residual_slices(rows: list, actual, predicted, minimum_group_size: int = 20) -> dict:
    """Aggregate held-out residuals by stable, point-in-time dimensions."""
    context = _row_context_lookup()
    groups: dict[str, dict[str, list[tuple[float, float]]]] = defaultdict(lambda: defaultdict(list))
    for row, target, pred in zip(rows, actual, predicted):
        game_date, player_id, features, _ = row
        extra = context.get((str(player_id), game_date), {})
        dimensions = {
            'player': extra.get('player', str(player_id)),
            'opponent': extra.get('opponent', 'unknown'),
            'season': _season(game_date),
            'home_away': 'home' if float(features.get('home_away', 0.0) or 0.0) >= 0.5 else 'away',
            'minutes_band': _minutes_band(float(extra.get('minutes', features.get('min_last_3_avg', 0.0)) or 0.0)),
            'games_played_band': _games_band(float(features.get('games_played', 0.0) or 0.0)),
        }
        for dimension, value in dimensions.items():
            groups[dimension][str(value)].append((float(target), float(pred)))

    result = {}
    for dimension, values in groups.items():
        result[dimension] = {}
        for value, pairs in values.items():
            if len(pairs) < minimum_group_size:
                continue
            y_true = [p[0] for p in pairs]
            y_pred = [p[1] for p in pairs]
            errors = [abs(a - p) for a, p in pairs]
            tail_cutoff = sorted(errors)[max(0, int(len(errors) * 0.9) - 1)]
            result[dimension][value] = {
                'n': len(pairs),
                'mae': round(_mae(y_true, y_pred), 4),
                'bias': round(_bias(y_true, y_pred), 4),
                'p90_abs_error': round(float(tail_cutoff), 4),
            }
    result['injury_status'] = {'availability': 'unavailable_without_point_in_time_history'}
    return result


def _learning_curve(model, X, y, train_idx, val_idx) -> list[dict]:
    from sklearn.base import clone

    curve = []
    for fraction in (0.25, 0.5, 0.75, 1.0):
        size = max(1, int(len(train_idx) * fraction))
        subset = train_idx[:size]
        candidate = clone(model)
        candidate.fit(X[subset], y[subset], eval_set=[(X[val_idx], y[val_idx])], verbose=False)
        curve.append({
            'fraction': fraction,
            'train_samples': len(subset),
            'train_mae': round(_mae(y[subset], candidate.predict(X[subset])), 4),
            'val_mae': round(_mae(y[val_idx], candidate.predict(X[val_idx])), 4),
        })
    return curve


def run_projection_diagnostics(stat_type: str, include_learning_curves: bool = False) -> dict:
    """Evaluate the active point model on its chronological holdout."""
    import numpy as np

    from app.services.ml_model import _build_training_rows, load_active_model

    model_name = f'projection_{stat_type}'
    meta = ModelMetadata.query.filter_by(model_name=model_name, is_active=True).first()
    run = begin_evaluation(
        'model_diagnostics', stat_type,
        {'learning_curves': include_learning_curves, 'train_fraction': 0.8},
        model_name=model_name,
        model_version=meta.version if meta else None,
    )
    try:
        model, feature_names = load_active_model(stat_type)
        if model is None or not feature_names:
            raise ValueError(f'No active {model_name} model')
        rows = _build_training_rows(stat_type, min_train_samples=0)
        train_idx, val_idx, cutoff = chronological_split(rows)
        if not train_idx or not val_idx:
            raise ValueError('Insufficient dated rows for diagnostics')
        X = np.array([[row[2].get(key, 0.0) for key in feature_names] for row in rows])
        y = np.array([row[3] for row in rows])
        pred_train = model.predict(X[train_idx])
        pred_val = model.predict(X[val_idx])
        metrics = regression_summary(y[train_idx], pred_train, y[val_idx], pred_val)
        metrics.update({
            'cutoff_date': cutoff.isoformat(),
            'train_samples': len(train_idx),
            'val_samples': len(val_idx),
            'residual_slices': residual_slices(
                [rows[i] for i in val_idx], y[val_idx], pred_val,
            ),
        })
        if include_learning_curves:
            metrics['learning_curve'] = _learning_curve(model, X, y, train_idx, val_idx)
        finish_evaluation(run, metrics, verdict='WARN' if metrics['likely_underfit'] else 'OK')
        if meta:
            metadata = json.loads(meta.metadata_json or '{}')
            metadata['diagnostics'] = {k: v for k, v in metrics.items() if k != 'residual_slices'}
            meta.metadata_json = json.dumps(metadata)
            db.session.commit()
        return metrics
    except Exception as exc:
        db.session.rollback()
        run = db.session.get(ModelEvaluationRun, run.id)
        fail_evaluation(run, str(exc))
        logger.exception('Model diagnostics failed for %s', stat_type)
        return {'error': str(exc), 'stat_type': stat_type}
