"""Multi-quantile XGBoost training for the Plan C distributional core.

Increment 1 covers the continuous-stat heads (points, rebounds, assists,
and PRA trained directly on realized pts+reb+ast). Count stats (threes,
steals, blocks) reuse the existing count:poisson point regressors from
app/services/ml_model.py unchanged — see distributional_predictor.py.

Training rows are built the same way app.services.ml_model._build_training_rows
builds them (same sliding window, same defense/game-total lookups, same
ml_feature_builder.build_ml_features_from_history call), so train/inference
feature parity with the point model is preserved. The only new mechanism is
_PRALogProxy, which lets the unchanged feature builder compute
avg_stat_last_5-style features against realized PRA instead of a single
stored column.
"""

import json
import logging
import os
from datetime import datetime, timezone

from app import db
from app.models import ModelMetadata, PlayerGameLog
from app.services.distribution import median_from_quantiles, rectify_quantiles
from app.services.distribution_calibration import collect_oof_pairs_quantile, fit_isotonic_calibrator
from app.services.ml_feature_builder import build_ml_features_from_history, build_team_game_aggregates
from app.services.ml_model import (
    MIN_TRAIN_SAMPLES,
    MODEL_DIR,
    _build_defense_lookup,
    _build_game_total_lookup,
    _check_training_data_quality,
    _ensure_model_dir,
)
from app.services.model_storage import persist_model_artifact
from app.utils.time_helpers import ET

logger = logging.getLogger(__name__)

QUANTILE_ALPHAS = [0.05, 0.15, 0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85, 0.95]
TRAIN_FRACTION = 0.70
CALIBRATION_FRACTION = 0.15
EARLY_STOPPING_FRACTION = 0.10

DIST_STAT_KEY_MAP = {
    'player_points': 'pts',
    'player_rebounds': 'reb',
    'player_assists': 'ast',
    'player_points_rebounds_assists': 'pra',
}
DIST_STAT_TYPES = list(DIST_STAT_KEY_MAP.keys())

# Count stats keep their existing count:poisson point regressor (ml_model.py)
# as the raw model; Increment 1 only adds a calibrator on top (see Task 7).
POISSON_DIST_STAT_TYPES = ['player_threes', 'player_steals', 'player_blocks']


class _PRALogProxy:
    """Wraps a PlayerGameLog row, exposing a computed ``pra`` attribute
    (pts+reb+ast) while delegating every other attribute unchanged.

    ml_feature_builder.build_ml_features_from_history is reused UNCHANGED
    for the PRA head (per the design spec); this proxy is the only new code
    needed to make its stat_key-driven features (avg_stat_last_5,
    std_stat_last_5, home/away/context splits, opponent history) operate on
    realized PRA instead of a single stored column.
    """

    def __init__(self, log):
        self._log = log

    @property
    def pra(self) -> float:
        return (
            float(getattr(self._log, 'pts', 0.0) or 0.0)
            + float(getattr(self._log, 'reb', 0.0) or 0.0)
            + float(getattr(self._log, 'ast', 0.0) or 0.0)
        )

    def __getattr__(self, name):
        return getattr(self._log, name)


def wrap_pra_logs(logs: list) -> list:
    """Wrap plain PlayerGameLog rows so stat_key='pra' features compute correctly."""
    return [_PRALogProxy(g) for g in logs]


def _date_cutoff_split(rows: list, frac: float = 0.8):
    """Chronological holdout split, mirroring ml_model.train_model's
    date_cutoff method (app/services/ml_model.py:356-378).

    ``rows`` are ``(date, player_id, features, target)`` tuples. Returns
    ``(train_idx, val_idx, split_method, cutoff_date)``. Falls back to a
    plain index split when fewer than 2 unique dates are present (e.g. tiny
    test fixtures).
    """
    unique_dates = sorted({r[0] for r in rows if r[0] is not None})
    train_idx: list = []
    val_idx: list = []
    cutoff_date = None
    split_method = 'date_cutoff'

    if len(unique_dates) >= 2:
        cutoff_idx = int(len(unique_dates) * frac) - 1
        cutoff_idx = max(0, min(cutoff_idx, len(unique_dates) - 2))
        cutoff_date = unique_dates[cutoff_idx]
        for idx, row in enumerate(rows):
            if row[0] is not None and row[0] <= cutoff_date:
                train_idx.append(idx)
            else:
                val_idx.append(idx)

    if not train_idx or len(val_idx) < 1:
        split_method = 'index_fallback'
        split_idx = int(len(rows) * frac)
        split_idx = min(max(split_idx, 1), len(rows) - 1)
        train_idx = list(range(split_idx))
        val_idx = list(range(split_idx, len(rows)))

    return train_idx, val_idx, split_method, cutoff_date


def _three_way_temporal_split(
    rows: list,
    train_frac: float,
    calib_frac: float,
):
    """Return disjoint, ordered train/calibration/test row indexes."""
    if train_frac <= 0 or calib_frac <= 0 or train_frac + calib_frac >= 1:
        raise ValueError("split fractions must leave non-empty train, calib, and test slices")

    unique_dates = sorted({row[0] for row in rows if row[0] is not None})
    if len(unique_dates) >= 3:
        train_count = max(1, min(int(len(unique_dates) * train_frac), len(unique_dates) - 2))
        calib_end = max(
            train_count + 1,
            min(int(len(unique_dates) * (train_frac + calib_frac)), len(unique_dates) - 1),
        )
        train_end = unique_dates[train_count - 1]
        calib_end_date = unique_dates[calib_end - 1]
        train_idx = [i for i, row in enumerate(rows) if row[0] is not None and row[0] <= train_end]
        calib_idx = [
            i for i, row in enumerate(rows)
            if row[0] is not None and train_end < row[0] <= calib_end_date
        ]
        test_idx = [i for i, row in enumerate(rows) if row[0] is None or row[0] > calib_end_date]
        method = 'date_cutoff'
        cutoffs = (train_end, calib_end_date)
    else:
        n = len(rows)
        train_end_idx = max(1, min(int(n * train_frac), n - 2))
        calib_end_idx = max(train_end_idx + 1, min(int(n * (train_frac + calib_frac)), n - 1))
        train_idx = list(range(train_end_idx))
        calib_idx = list(range(train_end_idx, calib_end_idx))
        test_idx = list(range(calib_end_idx, n))
        method = 'index_fallback'
        cutoffs = (None, None)
    return train_idx, calib_idx, test_idx, method, cutoffs


def _early_stopping_split(train_idx: list[int], eval_frac: float):
    """Carve the temporal tail of the training slice out for early stopping."""
    eval_count = max(1, int(len(train_idx) * eval_frac))
    eval_count = min(eval_count, len(train_idx) - 1)
    return train_idx[:-eval_count], train_idx[-eval_count:]


def _build_dist_training_rows(stat_type: str) -> list:
    """Dated training rows for one distributional stat type.

    Mirrors ml_model._build_training_rows (app/services/ml_model.py:190-255)
    exactly, but resolves the target (and the stat_key handed to the
    feature builder) via DIST_STAT_KEY_MAP, wrapping logs with
    _PRALogProxy for the PRA head. Returns [] for unsupported stat types or
    insufficient data.
    """
    stat_key = DIST_STAT_KEY_MAP.get(stat_type)
    if not stat_key:
        return []

    from app.services.historical_training_source import (
        load_historical_game_total_lookup,
        load_historical_training_logs,
    )

    all_logs = load_historical_training_logs()
    using_historical_source = bool(all_logs)
    if not using_historical_source:
        all_logs = (
            PlayerGameLog.query
            .order_by(PlayerGameLog.player_id, PlayerGameLog.game_date)
            .all()
        )
    if len(all_logs) < MIN_TRAIN_SAMPLES:
        logger.info(
            "Insufficient data for dist_%s model: %d rows (need %d)",
            stat_type, len(all_logs), MIN_TRAIN_SAMPLES,
        )
        return []

    quality = _check_training_data_quality(all_logs)
    if not quality['passed']:
        logger.warning(
            "Skipping dist_%s training due to data quality issues: %s",
            stat_type, quality['issues'],
        )
        return []

    if stat_key == 'pra':
        all_logs = wrap_pra_logs(all_logs)

    player_logs: dict = {}
    for log in all_logs:
        player_logs.setdefault(log.player_id, []).append(log)

    team_totals, team_counts = build_team_game_aggregates(all_logs)
    defense_lookup = _build_defense_lookup()
    game_total_lookup = (
        load_historical_game_total_lookup()
        if using_historical_source
        else _build_game_total_lookup()
    )

    rows = []
    for pid, logs in player_logs.items():
        logs = sorted(logs, key=lambda lg: ((lg.game_date is None), lg.game_date))
        if len(logs) < 10:
            continue

        for i in range(10, len(logs)):
            prior = logs[:i]
            current = logs[i]
            target = float(getattr(current, stat_key, 0.0) or 0.0)

            if using_historical_source:
                game_total_key = str(getattr(current, '_historical_game_id', '') or '')
            else:
                team_abbr = (getattr(current, 'team_abbr', '') or '').strip().upper()
                game_total_key = (current.game_date, team_abbr)
            game_total = game_total_lookup.get(game_total_key, 0.0)

            features = build_ml_features_from_history(
                prior_logs=prior,
                current_is_home=(current.home_away or '').lower() == 'home',
                stat_key=stat_key,
                team_totals=team_totals,
                team_counts=team_counts,
                current_game_date=current.game_date,
                current_matchup=current.matchup or '',
                game_total_line=game_total,
                defense_lookup=defense_lookup,
            )
            rows.append((current.game_date, str(pid), features, target))

    rows.sort(key=lambda r: ((r[0] is None), r[0], r[1]))
    return rows


def replay_running_baseline(row: tuple, stat_type: str) -> tuple[float, float] | None:
    """Replay the live heuristic projection using only information before the row date."""
    from app.services.projection_engine import ProjectionEngine
    from app.services.historical_training_source import (
        historical_training_store_has_rows,
        load_historical_replay_logs,
    )
    from app.services.stats_service import get_player_stats_summary

    game_date, player_id, features, _ = row
    if historical_training_store_has_rows():
        current, prior_logs = load_historical_replay_logs(player_id, game_date)
    else:
        current = PlayerGameLog.query.filter_by(
            player_id=str(player_id), game_date=game_date,
        ).first()
        prior_logs = (
            PlayerGameLog.query
            .filter(
                PlayerGameLog.player_id == str(player_id),
                PlayerGameLog.game_date < game_date,
            )
            .order_by(PlayerGameLog.game_date.desc())
            .limit(82)
            .all()
        )
    if current is None or not prior_logs:
        return None

    matchup = current.matchup or ''
    opponent = ''
    for separator in (' vs. ', ' @ '):
        if separator in matchup:
            opponent = matchup.split(separator, 1)[1]
            break
    is_home = (current.home_away or '').lower() == 'home'
    engine = ProjectionEngine()
    summary = get_player_stats_summary(str(player_id), prior_logs)
    engine._player_state_cache[current.player_name.strip().lower()] = (
        str(player_id), prior_logs, summary,
    )
    # The running product defaults USE_ML_PROJECTIONS=false; replay that exact baseline.
    engine._use_ml_projections = lambda: False
    result = engine.project_stat(
        current.player_name,
        stat_type,
        opponent_name=opponent,
        team_name=current.team_abbr or '',
        is_home=is_home,
        game_total_line=float(features.get('game_total_line', 0.0) or 0.0),
        game_date=game_date,
    )
    projection = float(result.get('projection', 0.0) or 0.0)
    std_dev = float(result.get('std_dev', 0.0) or 0.0)
    if projection <= 0 or std_dev <= 0:
        return None
    return projection, std_dev


def train_distributional_model(stat_type: str) -> dict:
    """Train a multi-quantile XGBoost head for one continuous stat type.

    Persists a new dist_<stat_type> ModelMetadata row (model_type
    'xgboost_quantile_regressor') via the existing model_storage layer.
    Also fits and persists the stat's out-of-fold isotonic calibrator.
    """
    try:
        from xgboost import XGBRegressor
        import numpy as np
    except ImportError:
        logger.error("xgboost not installed")
        return {'error': 'Missing ML dependencies'}

    if stat_type not in DIST_STAT_TYPES:
        return {'error': f'Unsupported distributional stat_type: {stat_type}', 'stat_type': stat_type}

    rows = _build_dist_training_rows(stat_type)
    if not rows:
        return {'error': 'Insufficient training data', 'stat_type': stat_type}

    feature_names = list(rows[0][2].keys())
    X = np.array([[row[2][k] for k in feature_names] for row in rows])
    y = np.array([row[3] for row in rows])

    train_idx, calib_idx, test_idx, split_method, cutoff_dates = _three_way_temporal_split(
        rows, train_frac=TRAIN_FRACTION, calib_frac=CALIBRATION_FRACTION,
    )
    fit_idx, early_stop_idx = _early_stopping_split(
        train_idx, eval_frac=EARLY_STOPPING_FRACTION,
    )
    if not fit_idx or not early_stop_idx or not calib_idx or not test_idx:
        return {'error': 'Insufficient validation data', 'stat_type': stat_type}

    X_train, X_early = X[fit_idx], X[early_stop_idx]
    y_train, y_early = y[fit_idx], y[early_stop_idx]
    X_calib, y_calib = X[calib_idx], y[calib_idx]

    xgb_params = dict(
        objective='reg:quantileerror',
        quantile_alpha=QUANTILE_ALPHAS,
        n_estimators=300,
        max_depth=5,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        reg_alpha=0.1,
        reg_lambda=1.0,
        random_state=42,
        n_jobs=1,
        early_stopping_rounds=25,
    )
    model = XGBRegressor(**xgb_params)
    model.fit(X_train, y_train, eval_set=[(X_early, y_early)], verbose=False)

    val_preds_raw = model.predict(X_calib)
    val_preds_rectified = [rectify_quantiles(row.tolist()) for row in val_preds_raw]
    val_medians = [median_from_quantiles(QUANTILE_ALPHAS, q) for q in val_preds_rectified]
    val_mae = float(np.mean(np.abs(np.array(val_medians) - y_calib)))
    calibration_pairs = collect_oof_pairs_quantile(
        list(zip([QUANTILE_ALPHAS] * len(val_preds_rectified), val_preds_rectified, y_calib.tolist())),
    )

    _ensure_model_dir()
    today = datetime.now(ET).date().isoformat()
    filename = f"dist_{stat_type}_{today}.json"
    filepath = os.path.join(MODEL_DIR, filename)
    model.save_model(filepath)
    artifact_path = persist_model_artifact(filepath, filename)

    try:
        engine = db.engine
        is_memory_sqlite = (
            engine.dialect.name == 'sqlite'
            and engine.url.query.get('mode') == 'memory'
        )
        db.session.remove()
        if not is_memory_sqlite:
            engine.dispose()
    except Exception:
        logger.warning("DB pool dispose failed before dist model write", exc_info=True)

    model_name = f"dist_{stat_type}"
    ModelMetadata.query.filter_by(model_name=model_name, is_active=True).update({'is_active': False})
    meta = ModelMetadata(
        model_name=model_name,
        model_type='xgboost_quantile_regressor',
        version=f"{stat_type}_{today}",
        file_path=artifact_path,
        training_date=datetime.now(timezone.utc),
        training_samples=len(X_train),
        val_mae=round(val_mae, 3),
        is_active=True,
        metadata_json=json.dumps({
            'feature_names': feature_names,
            'quantile_alphas': QUANTILE_ALPHAS,
            'val_samples': len(X_calib),
            'train_samples': len(X_train),
            'early_stopping_samples': len(X_early),
            'test_samples': len(test_idx),
            'split_method': split_method,
            'train_cutoff_date': cutoff_dates[0].isoformat() if cutoff_dates[0] else None,
            'calibration_cutoff_date': cutoff_dates[1].isoformat() if cutoff_dates[1] else None,
            'calibrator_model_name': f'dist_calibrator_{stat_type}',
        }),
    )
    db.session.add(meta)
    db.session.commit()

    calibrator_fitted = False
    calibrator_model_name = f'dist_calibrator_{stat_type}'
    try:
        calibrator = fit_isotonic_calibrator(calibration_pairs)
        import joblib
        calibrator_filename = f"{calibrator_model_name}_{today}.pkl"
        calibrator_filepath = os.path.join(MODEL_DIR, calibrator_filename)
        joblib.dump(calibrator, calibrator_filepath)
        calibrator_artifact_path = persist_model_artifact(calibrator_filepath, calibrator_filename)

        ModelMetadata.query.filter_by(model_name=calibrator_model_name, is_active=True).update({
            'is_active': False,
        })
        db.session.add(ModelMetadata(
            model_name=calibrator_model_name,
            model_type='isotonic_calibrator',
            version=f"{stat_type}_{today}",
            file_path=calibrator_artifact_path,
            training_date=datetime.now(timezone.utc),
            training_samples=len(calibration_pairs),
            is_active=True,
            metadata_json=json.dumps({'oof_pairs': len(calibration_pairs)}),
        ))
        db.session.commit()
        from app.services.distributional_predictor import load_calibrator
        load_calibrator.cache_clear()
        calibrator_fitted = True
    except ValueError:
        logger.warning("No OOF calibration pairs for dist_%s; skipping calibrator fit", stat_type)

    logger.info(
        "Trained dist_%s model: val_mae=%.3f, %d train / %d val samples",
        stat_type, val_mae, len(X_train), len(X_calib),
    )

    return {
        'stat_type': stat_type,
        'val_mae': round(val_mae, 3),
        'train_samples': len(X_train),
        'val_samples': len(X_calib),
        'model_path': artifact_path,
        'calibrator_fitted': calibrator_fitted,
        'calibration_pairs': len(calibration_pairs),
    }


def _collect_poisson_oof_rows(stat_type: str) -> list:
    """Return held-out ``(lambda, realized)`` rows from an active point model."""
    from app.services.ml_model import _build_training_rows as _build_point_training_rows
    from app.services.ml_model import load_active_model

    model, feature_names = load_active_model(stat_type)
    if model is None or feature_names is None:
        return []

    # An active model may have been trained with a lower explicit threshold
    # (notably the small offline fixture). OOF reconstruction should depend on
    # the active artifact, not today's training-admission threshold.
    rows = _build_point_training_rows(stat_type, min_train_samples=0)
    if not rows:
        return []

    _, calib_idx, _, _, _ = _three_way_temporal_split(
        rows, train_frac=TRAIN_FRACTION, calib_frac=CALIBRATION_FRACTION,
    )
    if not calib_idx:
        return []

    import numpy as np
    oof_rows = []
    for idx in calib_idx:
        _, _, features, target = rows[idx]
        X = np.array([[features.get(k, 0) for k in feature_names]])
        lam = float(model.predict(X)[0])
        if lam > 0:
            oof_rows.append((lam, target))
    return oof_rows


def train_distributional_calibrator_for_poisson_stat(stat_type: str) -> dict:
    """Fit and persist an isotonic calibrator over an incumbent Poisson head."""
    if stat_type not in POISSON_DIST_STAT_TYPES:
        return {'error': f'Unsupported poisson stat_type: {stat_type}', 'stat_type': stat_type}

    from app.services.distribution_calibration import collect_oof_pairs_poisson

    oof_rows = _collect_poisson_oof_rows(stat_type)
    if not oof_rows:
        return {'error': 'No OOF rows available', 'stat_type': stat_type}

    pairs = collect_oof_pairs_poisson(oof_rows)
    try:
        calibrator = fit_isotonic_calibrator(pairs)
    except ValueError:
        return {'error': 'No calibration pairs produced', 'stat_type': stat_type}

    import joblib
    _ensure_model_dir()
    today = datetime.now(ET).date().isoformat()
    model_name = f'dist_calibrator_{stat_type}'
    filename = f"{model_name}_{today}.pkl"
    filepath = os.path.join(MODEL_DIR, filename)
    joblib.dump(calibrator, filepath)
    artifact_path = persist_model_artifact(filepath, filename)

    ModelMetadata.query.filter_by(model_name=model_name, is_active=True).update({'is_active': False})
    db.session.add(ModelMetadata(
        model_name=model_name,
        model_type='isotonic_calibrator',
        version=f"{stat_type}_{today}",
        file_path=artifact_path,
        training_date=datetime.now(timezone.utc),
        training_samples=len(pairs),
        is_active=True,
        metadata_json=json.dumps({'oof_pairs': len(pairs), 'oof_rows': len(oof_rows)}),
    ))
    db.session.commit()
    from app.services.distributional_predictor import load_calibrator
    load_calibrator.cache_clear()

    return {
        'stat_type': stat_type,
        'calibration_pairs': len(pairs),
        'oof_rows': len(oof_rows),
        'model_path': artifact_path,
    }


def retrain_all_distributional_models() -> dict:
    """Retrain all distributional heads and their calibrators."""
    results = {}
    for stat_type in DIST_STAT_TYPES:
        results[stat_type] = train_distributional_model(stat_type)
    for stat_type in POISSON_DIST_STAT_TYPES:
        results[stat_type] = train_distributional_calibrator_for_poisson_stat(stat_type)
    return results


def backtest_verdict(dist_ece: float, gauss_ece: float, gate: float = 0.03) -> str:
    """Recommend promotion only when distributional ECE clears both comparisons."""
    if dist_ece <= gate and dist_ece <= gauss_ece:
        return 'PROMOTE'
    return 'HOLD'
