"""Compare point-model candidates without changing active model artifacts."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
from sklearn.metrics import mean_absolute_error
from xgboost import XGBRegressor

from app.services.ml_feature_builder import FEATURE_KEYS
from app.services.ml_model import (
    _build_training_rows, _projection_validation_indices,
    _sample_weights_by_recency,
)


DEFENSE_KEYS = ('opp_def_rating', 'opp_pace', 'opp_stat_allowed')


def _fit_and_score(x_fit, x_early, x_test, y_fit, y_early, y_test,
                   weights, params):
    model = XGBRegressor(**params)
    model.fit(x_fit, y_fit, sample_weight=weights,
              eval_set=[(x_early, y_early)], verbose=False)
    predictions = model.predict(x_test)
    return {
        'mae': round(float(mean_absolute_error(y_test, predictions)), 4),
        'bias': round(float(np.mean(predictions - y_test)), 4),
        'p90_absolute_error': round(float(np.quantile(np.abs(predictions - y_test), 0.9)), 4),
        'best_iteration': int(model.best_iteration),
    }


def evaluate() -> dict:
    rows = _build_training_rows('player_points')
    train_indices, test_indices, split_method, cutoff = _projection_validation_indices(rows)
    if split_method != 'date_cutoff' or not train_indices or not test_indices:
        raise ValueError('A dated training and test split is required')

    early_dates = sorted({rows[index][0] for index in train_indices})
    early_cut = early_dates[max(0, int(len(early_dates) * 0.9) - 1)]
    fit_indices = [index for index in train_indices if rows[index][0] <= early_cut]
    early_indices = [index for index in train_indices if rows[index][0] > early_cut]
    if not fit_indices or not early_indices:
        raise ValueError('Insufficient dated rows for early stopping')

    matrix = np.asarray([[row[2][key] for key in FEATURE_KEYS] for row in rows], dtype=np.float32)
    if np.any(matrix[:, FEATURE_KEYS.index('game_total_line')]):
        raise ValueError('Historical closing totals reached the decision-time feature set')
    targets = np.asarray([row[3] for row in rows], dtype=np.float32)
    weights = np.asarray(_sample_weights_by_recency(rows, max_boost=0.75), dtype=np.float32)
    no_defense = matrix.copy()
    no_defense[:, [FEATURE_KEYS.index(key) for key in DEFENSE_KEYS]] = 0

    params = dict(
        n_estimators=500, max_depth=5, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.8, reg_alpha=0.1,
        reg_lambda=1.0, random_state=42, early_stopping_rounds=25,
        objective='reg:pseudohubererror', n_jobs=4,
    )
    candidates = {
        'current_features_without_defense': (no_defense, params),
        'dated_defense_current_settings': (matrix, params),
        'dated_defense_simpler_trees': (
            matrix, {**params, 'max_depth': 3, 'min_child_weight': 5,
                     'n_estimators': 700},
        ),
    }
    results = {}
    for name, (features, config) in candidates.items():
        results[name] = _fit_and_score(
            features[fit_indices], features[early_indices], features[test_indices],
            targets[fit_indices], targets[early_indices], targets[test_indices],
            weights[fit_indices], config,
        )

    recent = matrix[test_indices, FEATURE_KEYS.index('avg_stat_last_5')]
    results['recent_five_baseline'] = {
        'mae': round(float(mean_absolute_error(targets[test_indices], recent)), 4),
    }
    return {
        'run_date_et': datetime.now(ZoneInfo('America/New_York')).date().isoformat(),
        'target': 'player_points', 'gate_mae': 3.5,
        'feature_count': len(FEATURE_KEYS),
        'fit_rows': len(fit_indices), 'early_stop_rows': len(early_indices),
        'test_rows': len(test_indices),
        'early_stop_through': early_cut.isoformat(),
        'test_after': cutoff.isoformat(),
        'dated_defense_coverage': int((matrix[:, FEATURE_KEYS.index('opp_stat_allowed')] > 0).sum()),
        'results': results,
        'verdict': 'research_only_no_model_promotion',
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()

    from dotenv import load_dotenv
    from app import create_app

    load_dotenv('.env')
    app = create_app()
    with app.app_context():
        report = evaluate()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
