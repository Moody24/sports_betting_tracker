"""Pick quality classifier (Model 2).

Learns from resolved bet history to predict which picks are likely to
win.  Unlike Model 1 (stat projection), this model learns *your*
betting patterns -- which combinations of factors produce winning picks
and which are traps.

Requires 200+ resolved picks before training has enough signal.
"""

import json
import logging
import os
from datetime import datetime, timezone, date as date_type

from app import db
from app.models import Bet, PickContext, ModelMetadata

logger = logging.getLogger(__name__)

MODEL_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'ml_models')
MIN_RESOLVED_PICKS = 200

# Feature keys extracted from PickContext.context_json for training
PICK_FEATURES = [
    'projected_stat',
    'projected_edge',
    'model1_vs_line_diff',
    'player_variance',
    'player_games_this_season',
    'player_hit_rate_vs_line',
    'opp_defense_rating',
    'opp_pace',
    'opp_matchup_adj',
    'back_to_back',
    'home_game',
    'days_rest',
    'prop_line',
    'american_odds',
    'line_vs_season_avg',
]

# String features that need encoding
TREND_MAP = {'hot': 1, 'cold': -1, 'neutral': 0}
MINUTES_MAP = {'increasing': 1, 'stable': 0, 'decreasing': -1}
TIER_MAP = {'strong': 3, 'moderate': 2, 'slight': 1, 'no_edge': 0}


def _build_training_data():
    """Build training data from resolved bets that have PickContext.

    Returns (features_list, targets) or (None, None) if insufficient data.
    """
    # Get all resolved bets with pick context
    resolved = (
        db.session.query(Bet, PickContext)
        .join(PickContext, Bet.id == PickContext.bet_id)
        .filter(Bet.outcome.in_(['win', 'lose']))
        .all()
    )

    if len(resolved) < MIN_RESOLVED_PICKS:
        logger.info(
            "Insufficient resolved picks for Model 2: %d (need %d)",
            len(resolved), MIN_RESOLVED_PICKS,
        )
        return None, None

    features_list = []
    targets = []

    for bet_obj, pick_ctx in resolved:
        try:
            ctx = json.loads(pick_ctx.context_json) if pick_ctx.context_json else {}
        except (ValueError, TypeError):
            continue

        features = {}
        for key in PICK_FEATURES:
            val = ctx.get(key, 0)
            # Convert booleans to int
            if isinstance(val, bool):
                val = int(val)
            try:
                features[key] = float(val)
            except (ValueError, TypeError):
                features[key] = 0.0

        # Encode categorical features
        features['player_trend'] = TREND_MAP.get(ctx.get('player_last5_trend', ''), 0)
        features['minutes_trend'] = MINUTES_MAP.get(ctx.get('minutes_trend', ''), 0)
        features['confidence_tier_num'] = TIER_MAP.get(ctx.get('confidence_tier', ''), 0)
        features['injury_returning'] = int(ctx.get('injury_returning', False))

        features_list.append(features)
        targets.append(1 if bet_obj.outcome == 'win' else 0)

    if not features_list:
        return None, None

    return features_list, targets


def train_pick_quality_model() -> dict:
    """Train the pick quality XGBoost classifier.

    Returns a dict with training results metadata.
    """
    try:
        from xgboost import XGBClassifier
        from sklearn.metrics import accuracy_score, log_loss
        import numpy as np
    except ImportError:
        logger.error("xgboost or scikit-learn not installed")
        return {'error': 'Missing ML dependencies'}

    features_list, targets = _build_training_data()
    if features_list is None:
        return {'error': 'Insufficient training data', 'resolved_picks': 0}

    feature_names = list(features_list[0].keys())
    X = np.array([[f[k] for k in feature_names] for f in features_list])
    y = np.array(targets)

    # Stratified split (70/30)
    from sklearn.model_selection import train_test_split
    X_train, X_val, y_train, y_val = train_test_split(
        X, y, test_size=0.3, stratify=y, random_state=42,
    )

    # Handle class imbalance
    pos_count = sum(y_train)
    neg_count = len(y_train) - pos_count
    scale_pos = neg_count / max(pos_count, 1)

    model = XGBClassifier(
        n_estimators=150,
        max_depth=4,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.7,
        reg_alpha=0.2,
        reg_lambda=1.5,
        scale_pos_weight=scale_pos,
        eval_metric='logloss',
        use_label_encoder=False,
    )

    model.fit(
        X_train, y_train,
        eval_set=[(X_val, y_val)],
        verbose=False,
    )

    # Evaluate
    y_pred = model.predict(X_val)
    y_prob = model.predict_proba(X_val)[:, 1]
    accuracy = accuracy_score(y_val, y_pred)
    logloss = log_loss(y_val, y_prob)

    # Feature importance
    importance = dict(zip(feature_names, [float(v) for v in model.feature_importances_]))
    top_features = sorted(importance.items(), key=lambda x: x[1], reverse=True)[:10]

    # Save model
    os.makedirs(MODEL_DIR, exist_ok=True)
    today = date_type.today().isoformat()
    filename = f"pick_quality_nba_{today}.json"
    filepath = os.path.join(MODEL_DIR, filename)
    model.save_model(filepath)

    # Store metadata
    ModelMetadata.query.filter_by(
        model_name='pick_quality_nba', is_active=True,
    ).update({'is_active': False})

    meta = ModelMetadata(
        model_name='pick_quality_nba',
        model_type='xgboost_classifier',
        version=f"pick_quality_{today}",
        file_path=filepath,
        training_date=datetime.now(timezone.utc),
        training_samples=len(X_train),
        val_accuracy=round(accuracy, 4),
        is_active=True,
        metadata_json=json.dumps({
            'feature_names': feature_names,
            'val_samples': len(X_val),
            'logloss': round(logloss, 4),
            'top_features': top_features,
        }),
    )
    db.session.add(meta)
    db.session.commit()

    logger.info(
        "Trained pick quality model: accuracy=%.3f, logloss=%.3f, %d samples",
        accuracy, logloss, len(X_train),
    )

    return {
        'accuracy': round(accuracy, 4),
        'logloss': round(logloss, 4),
        'train_samples': len(X_train),
        'val_samples': len(X_val),
        'top_features': top_features,
        'model_path': filepath,
    }


def predict_pick_quality(context: dict) -> dict:
    """Predict whether a pick is likely to win.

    *context* is the same dict format as ``PickContext.context_json``.

    Returns {
        win_probability: float (0..1),
        recommendation: str ('take_it', 'caution', 'skip'),
        red_flags: list[str],
    }
    """
    try:
        from xgboost import XGBClassifier
        import numpy as np
    except ImportError:
        return _no_model_result()

    meta = ModelMetadata.query.filter_by(
        model_name='pick_quality_nba', is_active=True,
    ).first()

    if not meta or not os.path.exists(meta.file_path):
        return _no_model_result()

    try:
        md = json.loads(meta.metadata_json) if meta.metadata_json else {}
        feature_names = md.get('feature_names', [])
    except (ValueError, TypeError):
        return _no_model_result()

    if not feature_names:
        return _no_model_result()

    model = XGBClassifier()
    model.load_model(meta.file_path)

    # Build feature vector from context
    features = {}
    for key in PICK_FEATURES:
        val = context.get(key, 0)
        if isinstance(val, bool):
            val = int(val)
        try:
            features[key] = float(val)
        except (ValueError, TypeError):
            features[key] = 0.0

    features['player_trend'] = TREND_MAP.get(context.get('player_last5_trend', ''), 0)
    features['minutes_trend'] = MINUTES_MAP.get(context.get('minutes_trend', ''), 0)
    features['confidence_tier_num'] = TIER_MAP.get(context.get('confidence_tier', ''), 0)
    features['injury_returning'] = int(context.get('injury_returning', False))

    X = np.array([[features.get(k, 0) for k in feature_names]])

    try:
        win_prob = float(model.predict_proba(X)[0][1])
    except Exception as exc:
        logger.error("Pick quality prediction failed: %s", exc)
        return _no_model_result()

    # Determine recommendation
    red_flags = []
    if context.get('back_to_back'):
        red_flags.append('back-to-back game')
    if context.get('player_variance', 0) > 8:
        red_flags.append('high player variance')
    if context.get('injury_returning'):
        red_flags.append('returning from injury')
    if context.get('player_last5_trend') == 'cold':
        red_flags.append('cold streak')

    if win_prob >= 0.60:
        recommendation = 'take_it'
    elif win_prob >= 0.50:
        recommendation = 'caution' if red_flags else 'take_it'
    else:
        recommendation = 'skip'

    return {
        'win_probability': round(win_prob, 3),
        'recommendation': recommendation,
        'red_flags': red_flags,
        'model_version': meta.version,
    }


def get_feature_importance() -> list:
    """Return feature importance rankings from the active model."""
    meta = ModelMetadata.query.filter_by(
        model_name='pick_quality_nba', is_active=True,
    ).first()

    if not meta or not meta.metadata_json:
        return []

    try:
        md = json.loads(meta.metadata_json)
        return md.get('top_features', [])
    except (ValueError, TypeError):
        return []


def _no_model_result() -> dict:
    return {
        'win_probability': 0.5,
        'recommendation': 'no_model',
        'red_flags': [],
        'model_version': None,
    }
