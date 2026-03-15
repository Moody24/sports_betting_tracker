"""Game-level market models and recommendations (moneyline + totals)."""

from __future__ import annotations

import json
import logging
import os
from datetime import date as date_type, datetime, timezone

from app import db
from app.models import GameSnapshot, ModelMetadata
from app.services.model_storage import materialize_model_artifact, persist_model_artifact

logger = logging.getLogger(__name__)

MODEL_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'ml_models')
MODEL_NAME_ML = 'market_moneyline_nba'
MODEL_NAME_TOTAL = 'market_total_ou_nba'
FEATURES = ['over_under_line', 'moneyline_home', 'moneyline_away', 'ml_gap_abs', 'implied_home']


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, '').strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("Invalid %s=%r; using default %.3f", name, raw, default)
        return default


def _decide_market_action(edge: float, confidence: float, min_edge: float, min_confidence: float) -> tuple[str, str]:
    if edge < min_edge and confidence < min_confidence:
        return 'pass', 'edge_and_confidence_below_threshold'
    if edge < min_edge:
        return 'pass', 'edge_below_threshold'
    if confidence < min_confidence:
        return 'pass', 'confidence_below_threshold'
    return 'bet', 'meets_thresholds'


def _calibration_bins(rows: list[tuple[float, int]], bins: int) -> list[dict]:
    if bins < 2:
        bins = 2
    if bins > 10:
        bins = 10
    out = []
    for idx in range(bins):
        low = idx / bins
        high = (idx + 1) / bins
        bucket = []
        for prob, outcome in rows:
            if idx == bins - 1:
                in_bin = low <= prob <= high
            else:
                in_bin = low <= prob < high
            if in_bin:
                bucket.append((prob, outcome))
        if not bucket:
            out.append({'range': f'{low:.2f}-{high:.2f}', 'count': 0})
            continue
        avg_pred = sum(p for p, _ in bucket) / len(bucket)
        win_rate = sum(y for _, y in bucket) / len(bucket)
        out.append({
            'range': f'{low:.2f}-{high:.2f}',
            'count': len(bucket),
            'avg_pred': round(avg_pred, 4),
            'win_rate': round(win_rate, 4),
            'gap': round(avg_pred - win_rate, 4),
        })
    return out


def _metadata_logloss(meta: ModelMetadata | None) -> float | None:
    if not meta or not meta.metadata_json:
        return None
    try:
        payload = json.loads(meta.metadata_json)
    except (TypeError, ValueError):
        return None
    value = payload.get('logloss')
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _implied_prob(american_odds: int | None) -> float:
    if american_odds is None:
        return 0.5
    if american_odds > 0:
        return 100.0 / (american_odds + 100.0)
    if american_odds < 0:
        return abs(american_odds) / (abs(american_odds) + 100.0)
    return 0.5


def _features_for_snapshot(snap: GameSnapshot) -> list[float]:
    ml_h = int(snap.moneyline_home or 0)
    ml_a = int(snap.moneyline_away or 0)
    return [
        float(snap.over_under_line or 0.0),
        float(ml_h),
        float(ml_a),
        abs(float(ml_h - ml_a)),
        float(_implied_prob(ml_h)),
    ]


def _split_time_aware(
    X: list[list[float]],
    y: list[int],
    game_dates: list[date_type | None],
) -> tuple[list[list[float]], list[list[float]], list[int], list[int], str]:
    """Prefer chronological split; fallback to stratified random if needed."""
    from sklearn.model_selection import train_test_split

    rows = sorted(
        zip(game_dates, X, y),
        key=lambda t: t[0] or date_type.min,
    )
    split_idx = int(len(rows) * 0.7)
    split_idx = max(1, min(split_idx, len(rows) - 1))
    train_rows = rows[:split_idx]
    val_rows = rows[split_idx:]

    X_train = [r[1] for r in train_rows]
    y_train = [r[2] for r in train_rows]
    X_val = [r[1] for r in val_rows]
    y_val = [r[2] for r in val_rows]

    if len(set(y_train)) >= 2 and len(set(y_val)) >= 2:
        return X_train, X_val, y_train, y_val, 'time_aware'

    X_train, X_val, y_train, y_val = train_test_split(
        X, y, test_size=0.3, random_state=42, stratify=y,
    )
    return X_train, X_val, y_train, y_val, 'stratified_fallback'


def _predict_prob_one(model_obj, row: list[list[float]]) -> float:
    """Return positive-class probability for either raw or calibrated payload."""
    if isinstance(model_obj, dict):
        base_model = model_obj.get('model')
        calibrator = model_obj.get('calibrator')
        p = float(base_model.predict_proba(row)[0][1])
        if calibrator is not None:
            p = float(calibrator.predict([p])[0])
            return max(0.0, min(1.0, p))
        return p
    return float(model_obj.predict_proba(row)[0][1])


def _train_one(
    model_name: str,
    X: list[list[float]],
    y: list[int],
    game_dates: list[date_type | None],
    min_samples: int,
) -> dict:
    if len(X) < min_samples:
        return {'error': f'insufficient_samples:{len(X)}'}
    if len(set(y)) < 2:
        return {'error': 'single_class_target'}

    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import accuracy_score, log_loss
    from sklearn.isotonic import IsotonicRegression
    import joblib

    X_train, X_val, y_train, y_val, split_strategy = _split_time_aware(
        X=X, y=y, game_dates=game_dates,
    )
    model = LogisticRegression(max_iter=400, class_weight='balanced')
    model.fit(X_train, y_train)
    raw_probs = model.predict_proba(X_val)[:, 1]
    calibrator = None
    calibration_method = 'none'
    if len(X_val) >= 40 and len(set(y_val)) >= 2:
        calibrator = IsotonicRegression(out_of_bounds='clip')
        calibrator.fit(raw_probs, y_val)
        probs = calibrator.predict(raw_probs)
        calibration_method = 'isotonic'
    else:
        probs = raw_probs
    pred = (probs >= 0.5).astype(int)
    acc = float(accuracy_score(y_val, pred))
    ll = float(log_loss(y_val, probs))

    os.makedirs(MODEL_DIR, exist_ok=True)
    today = date_type.today().isoformat()
    filename = f'{model_name}_{today}.pkl'
    filepath = os.path.join(MODEL_DIR, filename)
    payload = {'model': model, 'calibrator': calibrator} if calibrator is not None else model
    joblib.dump(payload, filepath)
    artifact_path = persist_model_artifact(filepath, filename)

    ModelMetadata.query.filter_by(model_name=model_name, is_active=True).update({'is_active': False})
    meta = ModelMetadata(
        model_name=model_name,
        model_type='sklearn_logreg',
        version=f'{model_name}_{today}',
        file_path=artifact_path,
        training_date=datetime.now(timezone.utc),
        training_samples=len(X_train),
        val_accuracy=round(acc, 4),
        is_active=True,
        metadata_json=json.dumps({
            'feature_names': FEATURES,
            'logloss': round(ll, 4),
            'val_samples': len(X_val),
            'split_strategy': split_strategy,
            'calibration_method': calibration_method,
        }),
    )
    db.session.add(meta)
    db.session.commit()
    return {
        'accuracy': round(acc, 4),
        'logloss': round(ll, 4),
        'version': meta.version,
        'split_strategy': split_strategy,
        'calibration_method': calibration_method,
    }


def train_market_models(min_samples: int = 60) -> dict:
    """Train moneyline and totals models from final GameSnapshot rows."""
    snaps = (
        GameSnapshot.query
        .filter(GameSnapshot.is_final.is_(True))
        .filter(GameSnapshot.home_score.isnot(None))
        .filter(GameSnapshot.away_score.isnot(None))
        .filter(GameSnapshot.over_under_line.isnot(None))
        .filter(GameSnapshot.moneyline_home.isnot(None))
        .filter(GameSnapshot.moneyline_away.isnot(None))
        .all()
    )

    X_ml, y_ml, d_ml = [], [], []
    X_tot, y_tot, d_tot = [], [], []
    for s in snaps:
        total_score = (s.home_score or 0) + (s.away_score or 0)
        if s.home_score != s.away_score:
            X_ml.append(_features_for_snapshot(s))
            y_ml.append(1 if (s.home_score or 0) > (s.away_score or 0) else 0)
            d_ml.append(s.game_date)
        if total_score != float(s.over_under_line or 0):
            X_tot.append(_features_for_snapshot(s))
            y_tot.append(1 if total_score > float(s.over_under_line or 0) else 0)
            d_tot.append(s.game_date)

    return {
        'moneyline': _train_one(
            MODEL_NAME_ML, X_ml, y_ml, game_dates=d_ml, min_samples=min_samples,
        ),
        'total_ou': _train_one(
            MODEL_NAME_TOTAL, X_tot, y_tot, game_dates=d_tot, min_samples=min_samples,
        ),
        'rows_scanned': len(snaps),
    }


def _load_active_model(model_name: str):
    meta = ModelMetadata.query.filter_by(model_name=model_name, is_active=True).first()
    if not meta:
        return None, None
    local_path = materialize_model_artifact(meta.file_path)
    if not local_path:
        return None, None
    import joblib
    try:
        return joblib.load(local_path), meta
    except Exception as exc:
        logger.error('Failed to load %s: %s', model_name, exc)
        return None, None


def recommend_market_sides(games: list[dict]) -> dict[str, dict]:
    """Return recommendations keyed by espn_id for moneyline and total."""
    ml_model, ml_meta = _load_active_model(MODEL_NAME_ML)
    tot_model, tot_meta = _load_active_model(MODEL_NAME_TOTAL)
    if not ml_model and not tot_model:
        return {}

    min_ml_edge = _env_float('MARKET_REC_MIN_EDGE_ML', 0.03)
    min_ml_conf = _env_float('MARKET_REC_MIN_CONF_ML', 0.55)
    min_total_edge = _env_float('MARKET_REC_MIN_EDGE_TOTAL', 0.06)
    min_total_conf = _env_float('MARKET_REC_MIN_CONF_TOTAL', 0.56)

    out: dict[str, dict] = {}
    for g in games:
        espn_id = g.get('espn_id')
        if not espn_id:
            continue
        ou_line = g.get('over_under_line')
        ml_h = g.get('moneyline_home')
        ml_a = g.get('moneyline_away')
        if ou_line is None or ml_h is None or ml_a is None:
            continue
        row = [[float(ou_line), float(ml_h), float(ml_a), abs(float(ml_h - ml_a)), float(_implied_prob(int(ml_h)))]]
        entry = {}
        if ml_model:
            p_home = _predict_prob_one(ml_model, row)
            p_away = 1.0 - p_home
            edge_home = p_home - _implied_prob(int(ml_h))
            edge_away = p_away - _implied_prob(int(ml_a))
            side = 'home' if edge_home >= edge_away else 'away'
            edge = edge_home if side == 'home' else edge_away
            confidence = max(p_home, p_away)
            action, reason = _decide_market_action(
                edge=edge,
                confidence=confidence,
                min_edge=min_ml_edge,
                min_confidence=min_ml_conf,
            )
            entry['moneyline'] = {
                'side': side,
                'edge': round(edge, 3),
                'confidence': round(confidence, 3),
                'action': action,
                'action_reason': reason,
                'model_version': ml_meta.version if ml_meta else None,
            }
        if tot_model:
            p_over = _predict_prob_one(tot_model, row)
            side = 'over' if p_over >= 0.5 else 'under'
            confidence = max(p_over, 1.0 - p_over)
            edge = abs(p_over - 0.5)
            action, reason = _decide_market_action(
                edge=edge,
                confidence=confidence,
                min_edge=min_total_edge,
                min_confidence=min_total_conf,
            )
            entry['total'] = {
                'side': side,
                'confidence': round(confidence, 3),
                'action': action,
                'action_reason': reason,
                'edge': round(edge, 3),
                'model_version': tot_meta.version if tot_meta else None,
            }
        out[espn_id] = entry
    return out


def evaluate_market_models(days: int = 60, bins: int = 5) -> dict:
    """Evaluate active moneyline/total models on recent final snapshots."""
    from sklearn.metrics import accuracy_score, brier_score_loss, log_loss

    cutoff = date_type.today().toordinal() - max(1, int(days))
    snaps = (
        GameSnapshot.query
        .filter(GameSnapshot.is_final.is_(True))
        .filter(GameSnapshot.home_score.isnot(None))
        .filter(GameSnapshot.away_score.isnot(None))
        .filter(GameSnapshot.over_under_line.isnot(None))
        .filter(GameSnapshot.moneyline_home.isnot(None))
        .filter(GameSnapshot.moneyline_away.isnot(None))
        .all()
    )
    snaps = [s for s in snaps if s.game_date and s.game_date.toordinal() >= cutoff]

    ml_model, ml_meta = _load_active_model(MODEL_NAME_ML)
    tot_model, tot_meta = _load_active_model(MODEL_NAME_TOTAL)
    if not ml_model and not tot_model:
        return {'error': 'no_active_market_models', 'rows_scanned': len(snaps)}

    result = {
        'window_days': int(days),
        'rows_scanned': len(snaps),
        'as_of': datetime.now(timezone.utc).isoformat(),
        'markets': {},
    }

    def _evaluate(rows: list[dict], meta: ModelMetadata | None) -> dict:
        if not rows:
            return {'error': 'no_rows'}
        y_true = [int(r['y']) for r in rows]
        y_prob = [float(r['p']) for r in rows]
        y_pred = [1 if p >= 0.5 else 0 for p in y_prob]
        picks = [r for r in rows if r['action'] == 'bet']
        pick_hits = sum(1 for r in picks if int(r['pick_correct']) == 1)

        payload = {
            'rows': len(rows),
            'accuracy': round(float(accuracy_score(y_true, y_pred)), 4),
            'brier': round(float(brier_score_loss(y_true, y_prob)), 4),
            'avg_pred': round(float(sum(y_prob) / len(y_prob)), 4),
            'actual_rate': round(float(sum(y_true) / len(y_true)), 4),
            'overconfidence_gap': round(float((sum(y_prob) / len(y_prob)) - (sum(y_true) / len(y_true))), 4),
            'recommended_bets': len(picks),
            'recommended_bet_rate': round(float(len(picks) / len(rows)), 4),
            'recommended_hit_rate': round(float(pick_hits / len(picks)), 4) if picks else None,
            'bins': _calibration_bins([(r['p'], r['y']) for r in rows], bins=bins),
            'model_version': meta.version if meta else None,
        }
        try:
            payload['logloss'] = round(float(log_loss(y_true, y_prob)), 4)
        except ValueError:
            payload['logloss'] = None

        train_acc = float(meta.val_accuracy) if meta and meta.val_accuracy is not None else None
        train_ll = _metadata_logloss(meta)
        payload['train_val_accuracy'] = round(train_acc, 4) if train_acc is not None else None
        payload['train_val_logloss'] = round(train_ll, 4) if train_ll is not None else None
        payload['accuracy_delta'] = (
            round(payload['accuracy'] - train_acc, 4) if train_acc is not None else None
        )
        payload['logloss_delta'] = (
            round(payload['logloss'] - train_ll, 4)
            if payload.get('logloss') is not None and train_ll is not None else None
        )
        return payload

    if ml_model:
        min_ml_edge = _env_float('MARKET_REC_MIN_EDGE_ML', 0.03)
        min_ml_conf = _env_float('MARKET_REC_MIN_CONF_ML', 0.55)
        ml_rows = []
        for s in snaps:
            if s.home_score == s.away_score:
                continue
            row = [_features_for_snapshot(s)]
            p_home = _predict_prob_one(ml_model, row)
            p_away = 1.0 - p_home
            edge_home = p_home - _implied_prob(int(s.moneyline_home))
            edge_away = p_away - _implied_prob(int(s.moneyline_away))
            side = 'home' if edge_home >= edge_away else 'away'
            confidence = max(p_home, p_away)
            edge = edge_home if side == 'home' else edge_away
            action, _reason = _decide_market_action(edge, confidence, min_ml_edge, min_ml_conf)
            home_won = 1 if (s.home_score or 0) > (s.away_score or 0) else 0
            pick_correct = (
                (side == 'home' and home_won == 1)
                or (side == 'away' and home_won == 0)
            )
            ml_rows.append({'y': home_won, 'p': p_home, 'action': action, 'pick_correct': int(pick_correct)})
        result['markets']['moneyline'] = _evaluate(ml_rows, ml_meta)

    if tot_model:
        min_total_edge = _env_float('MARKET_REC_MIN_EDGE_TOTAL', 0.06)
        min_total_conf = _env_float('MARKET_REC_MIN_CONF_TOTAL', 0.56)
        tot_rows = []
        for s in snaps:
            total_score = (s.home_score or 0) + (s.away_score or 0)
            line = float(s.over_under_line or 0.0)
            if total_score == line:
                continue
            row = [_features_for_snapshot(s)]
            p_over = _predict_prob_one(tot_model, row)
            confidence = max(p_over, 1.0 - p_over)
            edge = abs(p_over - 0.5)
            side = 'over' if p_over >= 0.5 else 'under'
            action, _reason = _decide_market_action(edge, confidence, min_total_edge, min_total_conf)
            over_hit = 1 if total_score > line else 0
            pick_correct = (
                (side == 'over' and over_hit == 1)
                or (side == 'under' and over_hit == 0)
            )
            tot_rows.append({'y': over_hit, 'p': p_over, 'action': action, 'pick_correct': int(pick_correct)})
        result['markets']['total_ou'] = _evaluate(tot_rows, tot_meta)

    return result
