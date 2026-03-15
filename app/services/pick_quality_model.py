"""Pick quality classifier (Model 2).

Learns from resolved bet history to predict which picks are likely to
win. Supports both a global model and user-specific models when enough
per-user data exists.

Requires 100+ resolved picks before training has enough signal (see MIN_RESOLVED_PICKS).
"""

import glob
import json
import logging
import os
import math
from datetime import datetime, timezone, date as date_type
from typing import Optional

from app import db
from app.models import Bet, PickContext, ModelMetadata
from app.services.model_storage import materialize_model_artifact, persist_model_artifact, storage_mode

logger = logging.getLogger(__name__)

MODEL_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'ml_models')
MIN_RESOLVED_PICKS = 100

# Feature keys extracted from PickContext.context_json for training.
# Entries added later (minutes_volatility, stat_attempts_volatility) will be
# zero for older PickContext rows — this is safe; XGBoost handles sparse
# signals gracefully and the model degrades to the existing features.
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
    # Volatility features: added via postmortem-informed feature engineering.
    # Zero for older contexts; XGBoost treats missing signal as neutral.
    'minutes_volatility',
    'stat_attempts_volatility',
]

# String features that need encoding
TREND_MAP = {'hot': 1, 'cold': -1, 'neutral': 0}
MINUTES_MAP = {'increasing': 1, 'stable': 0, 'decreasing': -1}
TIER_MAP = {'strong': 3, 'moderate': 2, 'slight': 1, 'no_edge': 0}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _stabilize_probability(p_raw: float, metadata: dict) -> float:
    """Apply conservative post-processing to reduce confidence inflation."""
    # Shrink toward 0.5 to reduce overconfident tails in drift periods.
    shrink = metadata.get('probability_shrink')
    if shrink is None:
        shrink = _env_float('MODEL2_PROBABILITY_SHRINK', 0.88)
    try:
        shrink = float(shrink)
    except (TypeError, ValueError):
        shrink = 0.88
    shrink = min(max(shrink, 0.5), 1.0)
    p = 0.5 + (p_raw - 0.5) * shrink

    # Optional signed bias correction (positive means historically overconfident).
    bias = metadata.get('calibration_bias', _env_float('MODEL2_CALIBRATION_BIAS', 0.0))
    try:
        p -= float(bias)
    except (TypeError, ValueError):
        pass

    return min(max(float(p), 0.001), 0.999)


def _model_name(user_id: int | None) -> str:
    if user_id is None:
        return 'pick_quality_nba'
    return f'pick_quality_nba_user_{int(user_id)}'


def _is_polluted_context(ctx: dict) -> bool:
    """Detect PickContext rows with missing/zeroed matchup data.

    Bootstrap and early auto-pick rows were generated with empty opponent_name
    and team_name, producing zeros for all matchup features.  Including these
    rows in training teaches the model to ignore matchup signals entirely and
    is the primary driver of model drift.
    """
    matchup_keys = ('opp_defense_rating', 'opp_pace', 'opp_matchup_adj')
    zeroed = sum(1 for k in matchup_keys if float(ctx.get(k, 0) or 0) == 0)
    return zeroed == len(matchup_keys)


def _build_training_data(user_id: int | None = None, include_bootstrap: bool = False):
    """Build training data from resolved bets that have PickContext.

    By default, excludes AUTO_BOOTSTRAP_HIDDEN synthetic bets and rows with
    polluted (all-zero) matchup context — these are the primary sources of
    model drift.  Pass ``include_bootstrap=True`` only if you explicitly want
    the synthetic rows (e.g. when real data is still sparse).

    Returns (features_list, targets) or (None, None) if insufficient data.
    """
    # Get all resolved bets with pick context
    query = (
        db.session.query(Bet, PickContext)
        .join(PickContext, Bet.id == PickContext.bet_id)
        .filter(Bet.outcome.in_(['win', 'lose']))
    )
    if user_id is not None:
        query = query.filter(Bet.user_id == int(user_id))
    if not include_bootstrap:
        query = query.filter(
            db.or_(Bet.notes.is_(None), ~Bet.notes.like('AUTO_BOOTSTRAP_HIDDEN%'))
        )
    resolved = query.all()

    if len(resolved) < MIN_RESOLVED_PICKS:
        logger.info(
            "Insufficient resolved picks for Model 2: %d (need %d)",
            len(resolved), MIN_RESOLVED_PICKS,
        )
        return None, None, None

    features_list = []
    targets = []
    _dates = []
    skipped_polluted = 0

    for bet_obj, pick_ctx in resolved:
        try:
            ctx = json.loads(pick_ctx.context_json) if pick_ctx.context_json else {}
        except (ValueError, TypeError):
            continue

        # Skip rows with polluted (all-zero) matchup context — these were
        # generated without opponent/team info and would teach the model to
        # ignore the most predictive features.
        if _is_polluted_context(ctx):
            skipped_polluted += 1
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
        # Preserve match_date for time-aware splitting — None-safe.
        _dates.append(getattr(bet_obj, 'match_date', None))

    if skipped_polluted:
        logger.info(
            "Model 2 training: skipped %d rows with polluted (all-zero) matchup context",
            skipped_polluted,
        )

    if not features_list:
        return None, None, None

    return features_list, targets, _dates


def train_pick_quality_model(user_id: int | None = None) -> dict:
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

    features_list, targets, dates = _build_training_data(user_id=user_id)
    if features_list is None:
        return {
            'error': 'Insufficient training data',
            'resolved_picks': 0,
            'user_id': user_id,
        }

    feature_names = list(features_list[0].keys())
    X = np.array([[f[k] for k in feature_names] for f in features_list])
    y = np.array(targets)

    # Time-aware split: sort by match_date, use last 30% as validation.
    # Enabled via MODEL2_TIME_AWARE_SPLIT=true; falls back to stratified random split.
    use_time_split = os.getenv('MODEL2_TIME_AWARE_SPLIT', 'false').lower() == 'true'
    split_method = 'time_ordered' if use_time_split else 'stratified_random'

    if use_time_split and dates and any(d is not None for d in dates):
        # Sort rows by date (None dates go last so they land in val set)
        order = sorted(range(len(dates)), key=lambda i: (dates[i] is None, dates[i]))
        X = X[order]
        y = y[order]
        split_idx = int(len(X) * 0.7)
        split_idx = max(1, min(split_idx, len(X) - 1))
        X_train, X_val = X[:split_idx], X[split_idx:]
        y_train, y_val = y[:split_idx], y[split_idx:]
        logger.info("Model 2 time-aware split: %d train / %d val", len(X_train), len(X_val))
    else:
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
        early_stopping_rounds=20,
    )

    model.fit(
        X_train, y_train,
        eval_set=[(X_val, y_val)],
        verbose=False,
    )

    # Calibrate with isotonic regression on the validation set (graceful fallback)
    calibration_method = 'none'
    final_model = model
    try:
        from sklearn.calibration import CalibratedClassifierCV
        # Isotonic regression overfits with small calibration sets (scikit-learn docs).
        # Use sigmoid (Platt scaling) unless we have a large enough calibration set.
        # Note: cv='prefit' is deprecated in newer scikit-learn — revisit when upgrading.
        method = 'isotonic' if len(X_val) >= 1000 else 'sigmoid'
        calibrated = CalibratedClassifierCV(model, method=method, cv='prefit')
        calibrated.fit(X_val, y_val)
        final_model = calibrated
        calibration_method = method
    except Exception as exc:
        logger.warning("Calibration failed; using uncalibrated model: %s", exc)

    # Evaluate the final model
    y_pred = final_model.predict(X_val)
    y_prob = final_model.predict_proba(X_val)[:, 1]
    accuracy = accuracy_score(y_val, y_pred)
    logloss = log_loss(y_val, y_prob)
    val_avg_pred = (sum(float(p) for p in y_prob) / len(y_prob)) if len(y_prob) else 0.5
    val_win_rate = (sum(float(v) for v in y_val) / len(y_val)) if len(y_val) else 0.5
    calibration_bias = round(val_avg_pred - val_win_rate, 4)

    # Feature importance from base XGBoost model
    importance = dict(zip(feature_names, [float(v) for v in model.feature_importances_]))
    top_features = sorted(importance.items(), key=lambda x: x[1], reverse=True)[:10]

    # Save model: calibrated → joblib .pkl; uncalibrated → XGBoost .json
    os.makedirs(MODEL_DIR, exist_ok=True)
    today = date_type.today().isoformat()
    model_name = _model_name(user_id)
    file_tag = model_name.replace('pick_quality_', '')
    try:
        import joblib
        filename = f"pick_quality_{file_tag}_{today}.pkl"
        filepath = os.path.join(MODEL_DIR, filename)
        joblib.dump(final_model, filepath)
    except Exception as exc:
        logger.warning("joblib save failed; falling back to JSON: %s", exc)
        filename = f"pick_quality_{file_tag}_{today}.json"
        filepath = os.path.join(MODEL_DIR, filename)
        model.save_model(filepath)
    artifact_path = persist_model_artifact(filepath, filename)

    # Store metadata — reconnect first; training can take minutes and Neon drops idle SSL connections.
    try:
        db.session.remove()
        db.engine.dispose()
    except Exception:
        pass
    ModelMetadata.query.filter_by(
        model_name=model_name, is_active=True,
    ).update({'is_active': False})

    meta = ModelMetadata(
        model_name=model_name,
        model_type='xgboost_classifier',
        version=f"{model_name}_{today}",
        file_path=artifact_path,
        training_date=datetime.now(timezone.utc),
        training_samples=len(X_train),
        val_accuracy=round(accuracy, 4),
        is_active=True,
        metadata_json=json.dumps({
            'feature_names': feature_names,
            'val_samples': len(X_val),
            'logloss': round(logloss, 4),
            'top_features': top_features,
            'calibration_method': calibration_method,
            'split_method': split_method,
            'calibration_bias': calibration_bias,
            'val_avg_pred': round(val_avg_pred, 4),
            'val_win_rate': round(val_win_rate, 4),
            'probability_shrink': round(_env_float('MODEL2_PROBABILITY_SHRINK', 0.88), 3),
            'take_it_threshold': round(_env_float('MODEL2_TAKE_IT_THRESHOLD', 0.60), 3),
            'caution_threshold': round(_env_float('MODEL2_CAUTION_THRESHOLD', 0.56), 3),
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
        'model_path': artifact_path,
        'user_id': user_id,
    }


def _find_local_model_fallback(model_name: str) -> Optional[str]:
    """Find the most recent local model file when the stored path is unavailable."""
    file_tag = model_name.replace('pick_quality_', '')
    for ext in ('pkl', 'json'):
        pattern = os.path.join(MODEL_DIR, f"pick_quality_{file_tag}_*.{ext}")
        files = sorted(glob.glob(pattern), reverse=True)
        if files:
            return files[0]
    return None


def predict_pick_quality(context: dict, user_id: int | None = None) -> dict:
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

    meta = None
    if user_id is not None:
        meta = ModelMetadata.query.filter_by(
            model_name=_model_name(user_id), is_active=True,
        ).first()
    if meta is None:
        meta = ModelMetadata.query.filter_by(
            model_name=_model_name(None), is_active=True,
        ).first()

    if not meta:
        return _no_model_result()
    local_model_path = materialize_model_artifact(meta.file_path)
    # S3 unavailable — scan for most recent local model file
    if not local_model_path:
        local_model_path = _find_local_model_fallback(_model_name(user_id))
    if not local_model_path:
        return _no_model_result()

    try:
        md = json.loads(meta.metadata_json) if meta.metadata_json else {}
        feature_names = md.get('feature_names', [])
    except (ValueError, TypeError):
        return _no_model_result()

    if not feature_names:
        return _no_model_result()

    # Load model: .pkl = joblib-serialized (calibrated), .json = XGBoost native
    if local_model_path.endswith('.pkl'):
        try:
            import joblib
            model = joblib.load(local_model_path)
        except Exception as exc:
            logger.error("Failed to load calibrated model: %s", exc)
            return _no_model_result()
    else:
        model = XGBClassifier()
        model.load_model(local_model_path)

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
        win_prob_raw = float(model.predict_proba(X)[0][1])
    except Exception as exc:
        logger.error("Pick quality prediction failed: %s", exc)
        return _no_model_result()
    win_prob = _stabilize_probability(win_prob_raw, md)

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

    take_it_threshold = md.get('take_it_threshold', _env_float('MODEL2_TAKE_IT_THRESHOLD', 0.60))
    caution_threshold = md.get('caution_threshold', _env_float('MODEL2_CAUTION_THRESHOLD', 0.56))
    try:
        take_it_threshold = float(take_it_threshold)
    except (TypeError, ValueError):
        take_it_threshold = 0.60
    try:
        caution_threshold = float(caution_threshold)
    except (TypeError, ValueError):
        caution_threshold = 0.56
    caution_threshold = min(caution_threshold, take_it_threshold)

    if win_prob >= take_it_threshold:
        recommendation = 'take_it'
    elif win_prob >= caution_threshold:
        recommendation = 'caution'
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
        model_name=_model_name(None), is_active=True,
    ).first()

    if not meta or not meta.metadata_json:
        return []

    try:
        md = json.loads(meta.metadata_json)
        return md.get('top_features', [])
    except (ValueError, TypeError):
        return []


def get_model_runtime_probe(user_id: int | None = None) -> dict:
    """Return runtime diagnostics for active Model 2 artifact availability.

    This is used by readiness endpoints to verify that the currently active
    pick-quality model can be resolved and loaded in the running process.
    """
    probe = {
        'model_name': _model_name(user_id),
        'storage_mode': storage_mode(),
        'active_model_found': False,
        'model_version': None,
        'path_scheme': None,
        'artifact_source': None,
        'artifact_basename': None,
        'model_loadable': False,
        'reason': 'unknown',
    }

    try:
        from xgboost import XGBClassifier
    except Exception:
        probe['reason'] = 'xgboost_missing'
        return probe

    meta = None
    if user_id is not None:
        meta = ModelMetadata.query.filter_by(
            model_name=_model_name(user_id), is_active=True,
        ).first()
    if meta is None:
        meta = ModelMetadata.query.filter_by(
            model_name=_model_name(None), is_active=True,
        ).first()

    if not meta:
        probe['reason'] = 'no_active_model'
        return probe

    probe['active_model_found'] = True
    probe['model_version'] = meta.version
    probe['path_scheme'] = 's3' if str(meta.file_path or '').startswith('s3://') else 'local'

    local_model_path = materialize_model_artifact(meta.file_path)
    if local_model_path:
        probe['artifact_source'] = 'configured_path'
    else:
        local_model_path = _find_local_model_fallback(_model_name(user_id))
        if local_model_path:
            probe['artifact_source'] = 'local_fallback'

    if not local_model_path:
        probe['reason'] = 'artifact_unavailable'
        return probe

    probe['artifact_basename'] = os.path.basename(local_model_path)

    # Validate loadability without running predictions.
    if local_model_path.endswith('.pkl'):
        try:
            import joblib
            joblib.load(local_model_path)
        except Exception as exc:
            probe['reason'] = f'load_error:{type(exc).__name__}'
            return probe
    else:
        try:
            model = XGBClassifier()
            model.load_model(local_model_path)
        except Exception as exc:
            probe['reason'] = f'load_error:{type(exc).__name__}'
            return probe

    probe['model_loadable'] = True
    probe['reason'] = 'ok'
    return probe


def _no_model_result() -> dict:
    return {
        'win_probability': 0.5,
        'recommendation': 'no_model',
        'red_flags': [],
        'model_version': None,
    }


def get_calibration_report(
    limit: int = 500,
    bins: int = 5,
    user_id: int | None = None,
) -> dict:
    """Evaluate active pick-quality model calibration on resolved picks.

    Returns aggregate quality metrics and probability-bin calibration stats.
    """
    try:
        limit = max(int(limit), 1)
    except (TypeError, ValueError):
        limit = 500
    try:
        bins = max(min(int(bins), 10), 2)
    except (TypeError, ValueError):
        bins = 5

    query = (
        db.session.query(Bet, PickContext)
        .join(PickContext, Bet.id == PickContext.bet_id)
        .filter(Bet.outcome.in_(['win', 'lose']))
        .order_by(Bet.match_date.desc(), Bet.id.desc())
    )
    if user_id is not None:
        query = query.filter(Bet.user_id == int(user_id))

    rows = query.limit(limit).all()
    if not rows:
        return {'error': 'No resolved picks with context found.'}

    evaluated = []
    no_model_count = 0
    recommendation_counts = {'take_it': 0, 'caution': 0, 'skip': 0, 'no_model': 0}
    model_version = None

    for bet_obj, pick_ctx in rows:
        try:
            context = json.loads(pick_ctx.context_json) if pick_ctx.context_json else {}
        except (TypeError, ValueError):
            continue

        prediction = predict_pick_quality(context, user_id=user_id)
        recommendation = prediction.get('recommendation', 'no_model')
        recommendation_counts[recommendation] = recommendation_counts.get(recommendation, 0) + 1

        if recommendation == 'no_model' or prediction.get('model_version') is None:
            no_model_count += 1
            continue

        p_raw = prediction.get('win_probability', 0.5)
        try:
            p = float(p_raw)
        except (TypeError, ValueError):
            p = 0.5
        p = min(max(p, 0.001), 0.999)
        y = 1 if bet_obj.outcome == 'win' else 0
        evaluated.append((p, y))
        model_version = model_version or prediction.get('model_version')

    if not evaluated:
        return {
            'error': 'No evaluable predictions (active model unavailable).',
            'total_rows': len(rows),
            'no_model_count': no_model_count,
        }

    n = len(evaluated)
    wins = sum(y for _, y in evaluated)
    losses = n - wins
    avg_pred = sum(p for p, _ in evaluated) / n
    win_rate = wins / n

    brier = sum((p - y) ** 2 for p, y in evaluated) / n
    logloss = -sum(y * math.log(p) + (1 - y) * math.log(1 - p) for p, y in evaluated) / n

    bin_rows = []
    for idx in range(bins):
        start = idx / bins
        end = (idx + 1) / bins
        values = [(p, y) for p, y in evaluated if (start <= p < end) or (idx == bins - 1 and p == 1.0)]
        if not values:
            bin_rows.append({
                'range': f'{start:.2f}-{end:.2f}',
                'count': 0,
                'avg_pred': None,
                'win_rate': None,
                'gap': None,
            })
            continue

        b_count = len(values)
        b_avg = sum(p for p, _ in values) / b_count
        b_win = sum(y for _, y in values) / b_count
        bin_rows.append({
            'range': f'{start:.2f}-{end:.2f}',
            'count': b_count,
            'avg_pred': round(b_avg, 3),
            'win_rate': round(b_win, 3),
            'gap': round(b_avg - b_win, 3),
        })

    return {
        'model_version': model_version,
        'total_rows': len(rows),
        'evaluated': n,
        'no_model_count': no_model_count,
        'wins': wins,
        'losses': losses,
        'win_rate': round(win_rate, 3),
        'avg_pred': round(avg_pred, 3),
        'overconfidence_gap': round(avg_pred - win_rate, 3),
        'brier': round(brier, 4),
        'logloss': round(logloss, 4),
        'recommendation_counts': recommendation_counts,
        'bins': bin_rows,
    }
