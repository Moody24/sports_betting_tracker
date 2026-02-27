"""XGBoost projection model (Model 1).

Predicts actual stat values for player props using historical game log
features.  Separate models are trained per stat type (points, rebounds,
assists, threes).

Falls back to the weighted average projection engine when no trained
model is available or when data is insufficient.
"""

import json
import logging
import os
from datetime import datetime, timezone, date as date_type

from app import db
from app.models import ModelMetadata, PlayerGameLog

logger = logging.getLogger(__name__)

# Directory for saved model files
MODEL_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'ml_models')

# Stat types we build models for
STAT_TYPES = ['player_points', 'player_rebounds', 'player_assists', 'player_threes']

STAT_KEY_MAP = {
    'player_points': 'pts',
    'player_rebounds': 'reb',
    'player_assists': 'ast',
    'player_threes': 'fg3m',
}

# Minimum training samples required
MIN_TRAIN_SAMPLES = 500


def _ensure_model_dir():
    """Create the ml_models directory if it doesn't exist."""
    os.makedirs(MODEL_DIR, exist_ok=True)


def _build_training_data(stat_type: str):
    """Build feature matrix and target vector from cached game logs.

    Uses a walk-forward approach: for each game log entry, features are
    computed from prior games only (no future data leakage).
    """
    stat_key = STAT_KEY_MAP.get(stat_type, 'pts')

    # Get all cached game logs ordered by player and date
    all_logs = (
        PlayerGameLog.query
        .order_by(PlayerGameLog.player_id, PlayerGameLog.game_date)
        .all()
    )

    if len(all_logs) < MIN_TRAIN_SAMPLES:
        logger.info(
            "Insufficient data for %s model: %d rows (need %d)",
            stat_type, len(all_logs), MIN_TRAIN_SAMPLES,
        )
        return None, None

    # Group by player
    player_logs = {}
    for log in all_logs:
        player_logs.setdefault(log.player_id, []).append(log)

    features_list = []
    targets = []

    for pid, logs in player_logs.items():
        if len(logs) < 10:
            continue

        for i in range(10, len(logs)):
            # Features from games before index i
            prior = logs[:i]
            current = logs[i]

            target = getattr(current, stat_key, 0) or 0

            # Build features from prior games
            last_5 = prior[-5:]
            last_10 = prior[-10:]

            def _avg(game_list, key):
                vals = [getattr(g, key, 0) or 0 for g in game_list]
                return sum(vals) / len(vals) if vals else 0

            def _std(game_list, key):
                vals = [getattr(g, key, 0) or 0 for g in game_list]
                if len(vals) < 2:
                    return 0
                mean = sum(vals) / len(vals)
                return (sum((v - mean) ** 2 for v in vals) / len(vals)) ** 0.5

            def _sum(game_list, key):
                return sum(getattr(g, key, 0) or 0 for g in game_list)

            def _ratio_sum(game_list, num_key, den_key):
                den = _sum(game_list, den_key)
                if den <= 0:
                    return 0.0
                return _sum(game_list, num_key) / den

            def _true_shooting_pct(game_list):
                pts = _sum(game_list, 'pts')
                fga = _sum(game_list, 'fga')
                fta = _sum(game_list, 'fta')
                denom = 2 * (fga + 0.44 * fta)
                if denom <= 0:
                    return 0.0
                return pts / denom

            home_logs = [g for g in prior if (g.home_away or '').lower() == 'home']
            away_logs = [g for g in prior if (g.home_away or '').lower() == 'away']
            current_is_home = (current.home_away or '').lower() == 'home'
            context_logs = home_logs if current_is_home else away_logs

            features = {
                'avg_stat_last_5': _avg(last_5, stat_key),
                'avg_stat_last_10': _avg(last_10, stat_key),
                'avg_stat_season': _avg(prior, stat_key),
                'std_stat_last_5': _std(last_5, stat_key),
                'std_stat_last_10': _std(last_10, stat_key),
                'min_last_3_avg': _avg(prior[-3:], 'minutes'),
                'home_away': 1 if current_is_home else 0,
                'games_played': len(prior),
                'home_split_stat_avg': _avg(home_logs, stat_key),
                'away_split_stat_avg': _avg(away_logs, stat_key),
                'context_split_stat_avg': _avg(context_logs, stat_key),
                'fg_pct_last_10': _ratio_sum(last_10, 'fgm', 'fga'),
                'ts_pct_last_10': _true_shooting_pct(last_10),
                'fga_last_5_avg': _avg(last_5, 'fga'),
                'fg3a_last_5_avg': _avg(last_5, 'fg3a'),
                'fg3m_last_5_avg': _avg(last_5, 'fg3m'),
                'fta_last_5_avg': _avg(last_5, 'fta'),
            }

            features_list.append(features)
            targets.append(target)

    if not features_list:
        return None, None

    return features_list, targets


def train_model(stat_type: str) -> dict:
    """Train an XGBoost model for a specific stat type.

    Returns a dict with training results metadata.
    """
    try:
        from xgboost import XGBRegressor
        from sklearn.metrics import mean_absolute_error
        import numpy as np
    except ImportError:
        logger.error("xgboost or scikit-learn not installed")
        return {'error': 'Missing ML dependencies'}

    player_game_log_rows = PlayerGameLog.query.count()
    features_list, targets = _build_training_data(stat_type)
    if features_list is None:
        return {'error': 'Insufficient training data', 'stat_type': stat_type}

    # Convert to arrays
    feature_names = list(features_list[0].keys())
    X = np.array([[f[k] for k in feature_names] for f in features_list])
    y = np.array(targets)

    # Walk-forward split: 80/20 by time order
    split_idx = int(len(X) * 0.8)
    X_train, X_val = X[:split_idx], X[split_idx:]
    y_train, y_val = y[:split_idx], y[split_idx:]

    model = XGBRegressor(
        n_estimators=200,
        max_depth=5,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        reg_alpha=0.1,
        reg_lambda=1.0,
    )

    model.fit(
        X_train, y_train,
        eval_set=[(X_val, y_val)],
        verbose=False,
    )

    # Evaluate
    y_pred = model.predict(X_val)
    mae = mean_absolute_error(y_val, y_pred)

    # Save model
    _ensure_model_dir()
    today = date_type.today().isoformat()
    filename = f"projection_{stat_type}_{today}.json"
    filepath = os.path.join(MODEL_DIR, filename)
    model.save_model(filepath)

    # Store metadata
    version = f"{stat_type}_{today}"
    # Deactivate previous models for this stat type
    ModelMetadata.query.filter_by(
        model_name=f"projection_{stat_type}", is_active=True
    ).update({'is_active': False})

    meta = ModelMetadata(
        model_name=f"projection_{stat_type}",
        model_type='xgboost_regressor',
        version=version,
        file_path=filepath,
        training_date=datetime.now(timezone.utc),
        training_samples=len(X_train),
        val_mae=round(mae, 3),
        is_active=True,
        metadata_json=json.dumps({
            'feature_names': feature_names,
            'val_samples': len(X_val),
            'train_samples': len(X_train),
            'player_game_log_rows': player_game_log_rows,
        }),
    )
    db.session.add(meta)
    db.session.commit()

    logger.info(
        "Trained %s model: MAE=%.3f, %d train / %d val samples",
        stat_type, mae, len(X_train), len(X_val),
    )

    return {
        'stat_type': stat_type,
        'mae': round(mae, 3),
        'train_samples': len(X_train),
        'val_samples': len(X_val),
        'model_path': filepath,
    }


def load_active_model(stat_type: str):
    """Load the currently active XGBoost model for a stat type.

    Returns (model, feature_names) or (None, None) if no model exists.
    """
    try:
        from xgboost import XGBRegressor
    except ImportError:
        return None, None

    meta = ModelMetadata.query.filter_by(
        model_name=f"projection_{stat_type}",
        is_active=True,
    ).first()

    if not meta or not os.path.exists(meta.file_path):
        return None, None

    model = XGBRegressor()
    model.load_model(meta.file_path)

    feature_names = None
    if meta.metadata_json:
        try:
            md = json.loads(meta.metadata_json)
            feature_names = md.get('feature_names')
        except (ValueError, TypeError):
            pass

    return model, feature_names


def predict_stat(stat_type: str, features: dict) -> float:
    """Predict a stat value using the trained model.

    Returns 0 if no model is available (caller should fall back to
    weighted average projection).
    """
    model, feature_names = load_active_model(stat_type)
    if model is None or feature_names is None:
        return 0.0

    try:
        import numpy as np
        X = np.array([[features.get(k, 0) for k in feature_names]])
        prediction = float(model.predict(X)[0])
        return round(prediction, 1)
    except Exception as exc:
        logger.error("Prediction failed for %s: %s", stat_type, exc)
        return 0.0


def retrain_all_models() -> dict:
    """Retrain all stat-type models.  Called weekly by the scheduler."""
    results = {}
    for stat_type in STAT_TYPES:
        model_result = train_model(stat_type)
        results[stat_type] = model_result
        if model_result.get('error'):
            logger.info('Model %s skipped: %s', stat_type, model_result['error'])
        else:
            logger.info(
                'Model %s trained with %s samples (val=%s, mae=%s)',
                stat_type,
                model_result.get('train_samples', 0),
                model_result.get('val_samples', 0),
                model_result.get('mae'),
            )
    return results


def get_model_performance() -> list:
    """Return performance metrics for all active models."""
    models = ModelMetadata.query.filter_by(is_active=True).all()
    return [
        {
            'name': m.model_name,
            'version': m.version,
            'training_date': m.training_date.isoformat() if m.training_date else '',
            'mae': m.val_mae,
            'accuracy': m.val_accuracy,
            'samples': m.training_samples,
        }
        for m in models
    ]
