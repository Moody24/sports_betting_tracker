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
FEATURES = [
    'over_under_line',
    'moneyline_home',
    'moneyline_away',
    'ml_gap_abs',
    'implied_home',
    'implied_away',
    'implied_gap',
    'favorite_home_flag',
    'ou_centered_220',
]
DEFAULT_POLICY = {
    'moneyline': {'min_edge': 0.03, 'min_confidence': 0.55},
    'total_ou': {'min_edge': 0.06, 'min_confidence': 0.56},
}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, '').strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("Invalid %s=%r; using default %.3f", name, raw, default)
        return default


def _env_float_optional(name: str) -> float | None:
    raw = os.getenv(name, '').strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        logger.warning("Invalid %s=%r; ignoring override", name, raw)
        return None


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, '').strip().lower()
    if not raw:
        return default
    if raw in ('1', 'true', 'yes', 'on'):
        return True
    if raw in ('0', 'false', 'no', 'off'):
        return False
    logger.warning("Invalid %s=%r; using default %s", name, raw, default)
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


def _metadata_json(meta: ModelMetadata | None) -> dict:
    payload_raw = getattr(meta, 'metadata_json', None) if meta else None
    if not payload_raw:
        return {}
    try:
        payload = json.loads(payload_raw)
        return payload if isinstance(payload, dict) else {}
    except (TypeError, ValueError):
        return {}


def _resolve_market_policy(
    ml_meta: ModelMetadata | None,
    tot_meta: ModelMetadata | None,
    override: dict | None = None,
) -> dict:
    policy = {
        'moneyline': dict(DEFAULT_POLICY['moneyline']),
        'total_ou': dict(DEFAULT_POLICY['total_ou']),
    }

    ml_payload = _metadata_json(ml_meta).get('recommended_thresholds', {})
    tot_payload = _metadata_json(tot_meta).get('recommended_thresholds', {})
    if isinstance(ml_payload, dict):
        policy['moneyline']['min_edge'] = float(ml_payload.get('min_edge', policy['moneyline']['min_edge']))
        policy['moneyline']['min_confidence'] = float(
            ml_payload.get('min_confidence', policy['moneyline']['min_confidence']),
        )
    if isinstance(tot_payload, dict):
        policy['total_ou']['min_edge'] = float(tot_payload.get('min_edge', policy['total_ou']['min_edge']))
        policy['total_ou']['min_confidence'] = float(
            tot_payload.get('min_confidence', policy['total_ou']['min_confidence']),
        )

    env_ml_edge = _env_float_optional('MARKET_REC_MIN_EDGE_ML')
    env_ml_conf = _env_float_optional('MARKET_REC_MIN_CONF_ML')
    env_tot_edge = _env_float_optional('MARKET_REC_MIN_EDGE_TOTAL')
    env_tot_conf = _env_float_optional('MARKET_REC_MIN_CONF_TOTAL')
    if env_ml_edge is not None:
        policy['moneyline']['min_edge'] = env_ml_edge
    if env_ml_conf is not None:
        policy['moneyline']['min_confidence'] = env_ml_conf
    if env_tot_edge is not None:
        policy['total_ou']['min_edge'] = env_tot_edge
    if env_tot_conf is not None:
        policy['total_ou']['min_confidence'] = env_tot_conf

    if isinstance(override, dict):
        for key in ('moneyline', 'total_ou'):
            if isinstance(override.get(key), dict):
                if override[key].get('min_edge') is not None:
                    policy[key]['min_edge'] = float(override[key]['min_edge'])
                if override[key].get('min_confidence') is not None:
                    policy[key]['min_confidence'] = float(override[key]['min_confidence'])

    return policy


def _is_market_enabled(market_key: str, meta: ModelMetadata | None) -> bool:
    if market_key == 'moneyline':
        if not _env_bool('MONEYLINE_RECS_ENABLED', True):
            return False
    elif market_key == 'total_ou':
        if not _env_bool('TOTAL_RECS_ENABLED', True):
            return False
    payload = _metadata_json(meta)
    if bool(payload.get('disabled', False)):
        return False
    return True


def _profit_per_unit(american_odds: int, won: bool) -> float:
    if not won:
        return -1.0
    if american_odds > 0:
        return american_odds / 100.0
    if american_odds < 0:
        return 100.0 / abs(american_odds)
    return 1.0


def _implied_prob(american_odds: int | None) -> float:
    if american_odds is None:
        return 0.5
    if american_odds > 0:
        return 100.0 / (american_odds + 100.0)
    if american_odds < 0:
        return abs(american_odds) / (abs(american_odds) + 100.0)
    return 0.5


def _features_for_snapshot(snap: GameSnapshot) -> list[float]:
    return _features_for_inputs(
        over_under_line=float(snap.over_under_line or 0.0),
        moneyline_home=int(snap.moneyline_home or 0),
        moneyline_away=int(snap.moneyline_away or 0),
    )


def _features_for_inputs(over_under_line: float, moneyline_home: int, moneyline_away: int) -> list[float]:
    implied_home = float(_implied_prob(moneyline_home))
    implied_away = float(_implied_prob(moneyline_away))
    implied_gap = implied_home - implied_away
    favorite_home_flag = 1.0 if implied_home >= implied_away else 0.0
    return [
        float(over_under_line),
        float(moneyline_home),
        float(moneyline_away),
        abs(float(moneyline_home - moneyline_away)),
        implied_home,
        implied_away,
        implied_gap,
        favorite_home_flag,
        float(over_under_line) - 220.0,
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
    model_row = _adapt_row_to_model(model_obj, row)
    if isinstance(model_obj, dict):
        base_model = model_obj.get('model')
        calibrator = model_obj.get('calibrator')
        p = float(base_model.predict_proba(model_row)[0][1])
        if calibrator is not None:
            p = float(calibrator.predict([p])[0])
            return max(0.0, min(1.0, p))
        return p
    return float(model_obj.predict_proba(model_row)[0][1])


def _adapt_row_to_model(model_obj, row: list[list[float]]) -> list[list[float]]:
    """Adapt feature width for backward compatibility with older artifacts."""
    try:
        base_model = model_obj.get('model') if isinstance(model_obj, dict) else model_obj
        expected = int(getattr(base_model, 'n_features_in_', len(row[0])))
    except Exception:
        return row
    actual = len(row[0])
    if actual == expected:
        return row
    if actual > expected:
        return [row[0][:expected]]
    return [row[0] + ([0.0] * (expected - actual))]


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
    candidate_cs = [0.3, 1.0, 3.0]
    model = None
    raw_probs = None
    best_ll = None
    for c in candidate_cs:
        m = LogisticRegression(max_iter=500, class_weight='balanced', C=c)
        m.fit(X_train, y_train)
        p = m.predict_proba(X_val)[:, 1]
        ll = float(log_loss(y_val, p))
        if best_ll is None or ll < best_ll:
            best_ll = ll
            model = m
            raw_probs = p
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
            'best_c': float(model.C) if model is not None else None,
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
        'best_c': float(model.C) if model is not None else None,
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


def _load_recent_final_snapshots(days: int) -> list[GameSnapshot]:
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
    return [s for s in snaps if s.game_date and s.game_date.toordinal() >= cutoff]


def _build_market_eval_rows(snaps: list[GameSnapshot], ml_model, tot_model) -> dict[str, list[dict]]:
    out = {'moneyline': [], 'total_ou': []}
    if ml_model:
        for s in snaps:
            if s.home_score == s.away_score:
                continue
            row = [_features_for_snapshot(s)]
            p_home = _predict_prob_one(ml_model, row)
            p_away = 1.0 - p_home
            edge_home = p_home - _implied_prob(int(s.moneyline_home))
            edge_away = p_away - _implied_prob(int(s.moneyline_away))
            side = 'home' if edge_home >= edge_away else 'away'
            edge = edge_home if side == 'home' else edge_away
            confidence = max(p_home, p_away)
            home_won = 1 if (s.home_score or 0) > (s.away_score or 0) else 0
            pick_correct = (side == 'home' and home_won == 1) or (side == 'away' and home_won == 0)
            picked_odds = int(s.moneyline_home) if side == 'home' else int(s.moneyline_away)
            out['moneyline'].append({
                'y': home_won,
                'p': p_home,
                'edge': float(edge),
                'confidence': float(confidence),
                'pick_correct': int(pick_correct),
                'odds': picked_odds,
            })

    if tot_model:
        for s in snaps:
            total_score = (s.home_score or 0) + (s.away_score or 0)
            line = float(s.over_under_line or 0.0)
            if total_score == line:
                continue
            row = [_features_for_snapshot(s)]
            p_over = _predict_prob_one(tot_model, row)
            side = 'over' if p_over >= 0.5 else 'under'
            confidence = max(p_over, 1.0 - p_over)
            edge = abs(p_over - 0.5)
            over_hit = 1 if total_score > line else 0
            pick_correct = (side == 'over' and over_hit == 1) or (side == 'under' and over_hit == 0)
            out['total_ou'].append({
                'y': over_hit,
                'p': p_over,
                'edge': float(edge),
                'confidence': float(confidence),
                'pick_correct': int(pick_correct),
                'odds': -110,  # Conservative baseline when side-specific total odds are not persisted.
            })
    return out


def _evaluate_market_rows(
    rows: list[dict],
    meta: ModelMetadata | None,
    bins: int,
    min_edge: float,
    min_confidence: float,
) -> dict:
    from sklearn.metrics import accuracy_score, brier_score_loss, log_loss

    if not rows:
        return {'error': 'no_rows'}
    y_true = [int(r['y']) for r in rows]
    y_prob = [float(r['p']) for r in rows]
    y_pred = [1 if p >= 0.5 else 0 for p in y_prob]

    picks = []
    for r in rows:
        action, _reason = _decide_market_action(r['edge'], r['confidence'], min_edge, min_confidence)
        if action == 'bet':
            picks.append(r)

    pick_hits = sum(1 for r in picks if int(r['pick_correct']) == 1)
    units_profit = sum(_profit_per_unit(int(r['odds']), bool(r['pick_correct'])) for r in picks)
    avg_edge = (sum(float(r['edge']) for r in picks) / len(picks)) if picks else 0.0

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
        'units_profit': round(float(units_profit), 4),
        'roi_per_bet': round(float(units_profit / len(picks)), 4) if picks else None,
        'closing_edge_proxy': round(float(avg_edge), 4),
        'bins': _calibration_bins([(r['p'], r['y']) for r in rows], bins=bins),
        'model_version': meta.version if meta else None,
        'policy': {'min_edge': round(float(min_edge), 4), 'min_confidence': round(float(min_confidence), 4)},
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


def recommend_market_sides(games: list[dict]) -> dict[str, dict]:
    """Return recommendations keyed by espn_id for moneyline and total."""
    ml_model, ml_meta = _load_active_model(MODEL_NAME_ML)
    tot_model, tot_meta = _load_active_model(MODEL_NAME_TOTAL)
    if not ml_model and not tot_model:
        return {}

    policy = _resolve_market_policy(ml_meta, tot_meta)
    min_ml_edge = float(policy['moneyline']['min_edge'])
    min_ml_conf = float(policy['moneyline']['min_confidence'])
    min_total_edge = float(policy['total_ou']['min_edge'])
    min_total_conf = float(policy['total_ou']['min_confidence'])
    moneyline_enabled = _is_market_enabled('moneyline', ml_meta)
    total_enabled = _is_market_enabled('total_ou', tot_meta)

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
        row = [_features_for_inputs(float(ou_line), int(ml_h), int(ml_a))]
        entry = {}
        if ml_model:
            p_home = _predict_prob_one(ml_model, row)
            p_away = 1.0 - p_home
            edge_home = p_home - _implied_prob(int(ml_h))
            edge_away = p_away - _implied_prob(int(ml_a))
            side = 'home' if edge_home >= edge_away else 'away'
            edge = edge_home if side == 'home' else edge_away
            confidence = max(p_home, p_away)
            if moneyline_enabled:
                action, reason = _decide_market_action(
                    edge=edge,
                    confidence=confidence,
                    min_edge=min_ml_edge,
                    min_confidence=min_ml_conf,
                )
            else:
                action, reason = 'pass', 'market_disabled'
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
            if total_enabled:
                action, reason = _decide_market_action(
                    edge=edge,
                    confidence=confidence,
                    min_edge=min_total_edge,
                    min_confidence=min_total_conf,
                )
            else:
                action, reason = 'pass', 'market_disabled'
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
    """Evaluate active moneyline/total models on recent finals."""
    return _evaluate_market_models_with_policy(days=days, bins=bins, policy_override=None)

def _evaluate_market_models_with_policy(days: int, bins: int, policy_override: dict | None) -> dict:
    snaps = _load_recent_final_snapshots(days=days)
    ml_model, ml_meta = _load_active_model(MODEL_NAME_ML)
    tot_model, tot_meta = _load_active_model(MODEL_NAME_TOTAL)
    if not ml_model and not tot_model:
        return {'error': 'no_active_market_models', 'rows_scanned': len(snaps)}

    rows = _build_market_eval_rows(snaps, ml_model, tot_model)
    policy = _resolve_market_policy(ml_meta, tot_meta, override=policy_override)
    result = {
        'window_days': int(days),
        'rows_scanned': len(snaps),
        'as_of': datetime.now(timezone.utc).isoformat(),
        'policy_used': policy,
        'markets': {},
    }
    if ml_model:
        result['markets']['moneyline'] = _evaluate_market_rows(
            rows['moneyline'],
            ml_meta,
            bins=bins,
            min_edge=float(policy['moneyline']['min_edge']),
            min_confidence=float(policy['moneyline']['min_confidence']),
        )
    if tot_model:
        result['markets']['total_ou'] = _evaluate_market_rows(
            rows['total_ou'],
            tot_meta,
            bins=bins,
            min_edge=float(policy['total_ou']['min_edge']),
            min_confidence=float(policy['total_ou']['min_confidence']),
        )
    return result


def apply_market_threshold_policy(policy: dict) -> dict:
    """Persist tuned recommendation thresholds in active market model metadata."""
    updated = []
    for model_name, key in ((MODEL_NAME_ML, 'moneyline'), (MODEL_NAME_TOTAL, 'total_ou')):
        meta = ModelMetadata.query.filter_by(model_name=model_name, is_active=True).first()
        if not meta:
            continue
        payload = _metadata_json(meta)
        payload['recommended_thresholds'] = {
            'min_edge': float(policy[key]['min_edge']),
            'min_confidence': float(policy[key]['min_confidence']),
        }
        meta.metadata_json = json.dumps(payload)
        updated.append(model_name)
    db.session.commit()
    return {'updated_models': updated}


def set_market_enabled(market: str, enabled: bool) -> dict:
    """Persist market recommendation enabled/disabled flag in active metadata."""
    key = market.strip().lower()
    if key not in ('moneyline', 'total_ou'):
        return {'error': 'invalid_market'}
    model_name = MODEL_NAME_ML if key == 'moneyline' else MODEL_NAME_TOTAL
    meta = ModelMetadata.query.filter_by(model_name=model_name, is_active=True).first()
    if not meta:
        return {'error': 'no_active_model', 'market': key}
    payload = _metadata_json(meta)
    payload['disabled'] = not bool(enabled)
    meta.metadata_json = json.dumps(payload)
    db.session.commit()
    return {'market': key, 'enabled': bool(enabled), 'model_version': meta.version}


def guard_market_recommendations(
    days: int = 60,
    bins: int = 5,
    drift_threshold: float = 0.05,
    min_bets: int = 20,
    apply: bool = True,
) -> dict:
    """Disable markets with drift breach or negative ROI on adequate sample."""
    report = evaluate_market_models(days=days, bins=bins)
    if report.get('error'):
        return report

    decisions = {}
    wf = walkforward_market_report(days=min(max(days, 30), 365), train_days=60, test_days=14, step_days=14, bins=bins)
    for market in ('moneyline', 'total_ou'):
        m = report.get('markets', {}).get(market) or {}
        if m.get('error'):
            decisions[market] = {'decision': 'skip', 'reason': m.get('error')}
            continue
        rec_bets = int(m.get('recommended_bets') or 0)
        acc_delta = m.get('accuracy_delta')
        roi = m.get('roi_per_bet')
        drift_breach = acc_delta is not None and abs(float(acc_delta)) > float(drift_threshold)
        roi_breach = rec_bets >= int(min_bets) and roi is not None and float(roi) < 0.0
        wf_summary = (((wf.get('markets') or {}).get(market) or {}).get('summary') if not wf.get('error') else None) or {}
        wf_folds = int(wf_summary.get('folds') or 0)
        wf_roi = wf_summary.get('avg_roi_per_bet')
        wf_roi_breach = wf_folds >= 3 and wf_roi is not None and float(wf_roi) < 0.0
        disable = bool(drift_breach or roi_breach or wf_roi_breach)
        decisions[market] = {
            'decision': 'disable' if disable else 'keep_enabled',
            'drift_breach': drift_breach,
            'roi_breach': roi_breach,
            'walkforward_roi_breach': wf_roi_breach,
            'walkforward_avg_roi_per_bet': wf_roi,
            'walkforward_folds': wf_folds,
            'recommended_bets': rec_bets,
            'accuracy_delta': acc_delta,
            'roi_per_bet': roi,
        }

    applied = {}
    if apply:
        for market, d in decisions.items():
            if d.get('decision') == 'skip':
                continue
            enabled = d.get('decision') != 'disable'
            applied[market] = set_market_enabled(market, enabled=enabled)

    return {
        'window_days': int(days),
        'drift_threshold': float(drift_threshold),
        'min_bets': int(min_bets),
        'decisions': decisions,
        'applied': bool(apply),
        'apply_result': applied,
    }


def tune_market_thresholds(days: int = 180, bins: int = 5, min_bets: int = 40, apply: bool = True) -> dict:
    """Grid-search market recommendation thresholds for ROI + CLV proxy quality."""
    candidate_ml_edge = [0.015, 0.02, 0.03, 0.04, 0.05]
    candidate_ml_conf = [0.52, 0.55, 0.58, 0.6, 0.62]
    candidate_tot_edge = [0.04, 0.05, 0.06, 0.07, 0.08]
    candidate_tot_conf = [0.53, 0.56, 0.59, 0.62, 0.65]

    base_report = _evaluate_market_models_with_policy(days=days, bins=bins, policy_override=None)
    if base_report.get('error'):
        return base_report

    best_policy = {
        'moneyline': dict(base_report['policy_used']['moneyline']),
        'total_ou': dict(base_report['policy_used']['total_ou']),
    }
    search_space = {
        'moneyline': (candidate_ml_edge, candidate_ml_conf),
        'total_ou': (candidate_tot_edge, candidate_tot_conf),
    }
    selected = {}

    for market in ('moneyline', 'total_ou'):
        best_score = None
        best_metrics = None
        edges, confs = search_space[market]
        for edge in edges:
            for conf in confs:
                policy_try = {
                    'moneyline': dict(best_policy['moneyline']),
                    'total_ou': dict(best_policy['total_ou']),
                }
                policy_try[market]['min_edge'] = edge
                policy_try[market]['min_confidence'] = conf
                report = _evaluate_market_models_with_policy(days=days, bins=bins, policy_override=policy_try)
                metrics = report.get('markets', {}).get(market, {})
                rec_bets = int(metrics.get('recommended_bets') or 0)
                if metrics.get('error') or rec_bets < min_bets:
                    continue
                roi = float(metrics.get('roi_per_bet') or 0.0)
                clv_proxy = float(metrics.get('closing_edge_proxy') or 0.0)
                cal_penalty = abs(float(metrics.get('overconfidence_gap') or 0.0))
                score = roi + (0.35 * clv_proxy) - (0.25 * cal_penalty)
                if best_score is None or score > best_score:
                    best_score = score
                    best_metrics = metrics
                    best_policy[market]['min_edge'] = edge
                    best_policy[market]['min_confidence'] = conf
        selected[market] = {
            'selected': dict(best_policy[market]),
            'score': round(float(best_score), 5) if best_score is not None else None,
            'metrics': best_metrics,
        }

    final_report = _evaluate_market_models_with_policy(days=days, bins=bins, policy_override=best_policy)
    result = {
        'window_days': int(days),
        'min_bets': int(min_bets),
        'selected': selected,
        'policy': best_policy,
        'evaluation': final_report,
        'applied': False,
    }
    if apply:
        apply_result = apply_market_threshold_policy(best_policy)
        result['applied'] = True
        result['apply_result'] = apply_result
    return result


def walkforward_market_report(
    days: int = 180,
    train_days: int = 60,
    test_days: int = 14,
    step_days: int = 14,
    bins: int = 5,
) -> dict:
    """Walk-forward evaluation for market models using rolling date windows."""
    from datetime import timedelta
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import accuracy_score, brier_score_loss, log_loss
    from sklearn.isotonic import IsotonicRegression

    snaps = sorted(_load_recent_final_snapshots(days=days), key=lambda s: s.game_date or date_type.min)
    if len(snaps) < 40:
        return {'error': 'insufficient_rows', 'rows_scanned': len(snaps)}

    ml_model_meta = ModelMetadata.query.filter_by(model_name=MODEL_NAME_ML, is_active=True).first()
    tot_model_meta = ModelMetadata.query.filter_by(model_name=MODEL_NAME_TOTAL, is_active=True).first()
    policy = _resolve_market_policy(ml_model_meta, tot_model_meta)

    start_date = snaps[0].game_date
    end_date = snaps[-1].game_date
    if not start_date or not end_date:
        return {'error': 'invalid_dates', 'rows_scanned': len(snaps)}

    folds = []
    cur_test_start = start_date + timedelta(days=train_days)
    while cur_test_start <= end_date:
        train_start = cur_test_start - timedelta(days=train_days)
        test_end = cur_test_start + timedelta(days=test_days - 1)
        train = [s for s in snaps if s.game_date and train_start <= s.game_date < cur_test_start]
        test = [s for s in snaps if s.game_date and cur_test_start <= s.game_date <= test_end]
        if train and test:
            folds.append((train_start, cur_test_start, test_end, train, test))
        cur_test_start = cur_test_start + timedelta(days=step_days)

    if not folds:
        return {'error': 'no_folds', 'rows_scanned': len(snaps)}

    market_fold_metrics = {'moneyline': [], 'total_ou': []}

    def _fit_predict(train_rows: list[dict], test_rows: list[dict]) -> tuple[list[float], list[int]]:
        if len(train_rows) < 20 or len({r['y'] for r in train_rows}) < 2:
            return [], []
        X_train = [r['x'] for r in train_rows]
        y_train = [r['y'] for r in train_rows]
        X_test = [r['x'] for r in test_rows]
        y_test = [r['y'] for r in test_rows]
        model = LogisticRegression(max_iter=400, class_weight='balanced')
        model.fit(X_train, y_train)
        p_test = list(model.predict_proba(X_test)[:, 1])
        if len(test_rows) >= 20 and len(set(y_test)) >= 2:
            iso = IsotonicRegression(out_of_bounds='clip')
            train_probs = list(model.predict_proba(X_train)[:, 1])
            iso.fit(train_probs, y_train)
            p_test = [max(0.0, min(1.0, float(iso.predict([p])[0]))) for p in p_test]
        return p_test, y_test

    for train_start, test_start, test_end, train_snaps, test_snaps in folds:
        train_rows = {'moneyline': [], 'total_ou': []}
        test_rows = {'moneyline': [], 'total_ou': []}
        for s in train_snaps:
            if s.home_score != s.away_score:
                train_rows['moneyline'].append({
                    'x': _features_for_snapshot(s),
                    'y': 1 if (s.home_score or 0) > (s.away_score or 0) else 0,
                    'moneyline_home': int(s.moneyline_home),
                    'moneyline_away': int(s.moneyline_away),
                })
            total_score = (s.home_score or 0) + (s.away_score or 0)
            line = float(s.over_under_line or 0.0)
            if total_score != line:
                train_rows['total_ou'].append({
                    'x': _features_for_snapshot(s),
                    'y': 1 if total_score > line else 0,
                })
        for s in test_snaps:
            if s.home_score != s.away_score:
                test_rows['moneyline'].append({
                    'x': _features_for_snapshot(s),
                    'y': 1 if (s.home_score or 0) > (s.away_score or 0) else 0,
                    'moneyline_home': int(s.moneyline_home),
                    'moneyline_away': int(s.moneyline_away),
                })
            total_score = (s.home_score or 0) + (s.away_score or 0)
            line = float(s.over_under_line or 0.0)
            if total_score != line:
                test_rows['total_ou'].append({
                    'x': _features_for_snapshot(s),
                    'y': 1 if total_score > line else 0,
                })

        for market in ('moneyline', 'total_ou'):
            probs, y_true = _fit_predict(train_rows[market], test_rows[market])
            if not probs:
                continue
            y_pred = [1 if p >= 0.5 else 0 for p in probs]
            edges = []
            confidences = []
            pick_correct = []
            odds = []
            if market == 'moneyline':
                for p, row in zip(probs, test_rows[market]):
                    p_home = p
                    p_away = 1.0 - p_home
                    edge_home = p_home - _implied_prob(int(row['moneyline_home']))
                    edge_away = p_away - _implied_prob(int(row['moneyline_away']))
                    side = 'home' if edge_home >= edge_away else 'away'
                    edge = edge_home if side == 'home' else edge_away
                    confidences.append(max(p_home, p_away))
                    edges.append(edge)
                    odds.append(int(row['moneyline_home']) if side == 'home' else int(row['moneyline_away']))
                    home_won = int(row['y'])
                    pick_correct.append(1 if ((side == 'home' and home_won == 1) or (side == 'away' and home_won == 0)) else 0)
                p_policy = policy['moneyline']
            else:
                for p, row in zip(probs, test_rows[market]):
                    side = 'over' if p >= 0.5 else 'under'
                    edges.append(abs(p - 0.5))
                    confidences.append(max(p, 1 - p))
                    odds.append(-110)
                    over_hit = int(row['y'])
                    pick_correct.append(1 if ((side == 'over' and over_hit == 1) or (side == 'under' and over_hit == 0)) else 0)
                p_policy = policy['total_ou']

            bet_idx = [
                i for i, (e, c) in enumerate(zip(edges, confidences))
                if _decide_market_action(
                    edge=float(e),
                    confidence=float(c),
                    min_edge=float(p_policy['min_edge']),
                    min_confidence=float(p_policy['min_confidence']),
                )[0] == 'bet'
            ]
            units_profit = sum(_profit_per_unit(int(odds[i]), bool(pick_correct[i])) for i in bet_idx)
            market_fold_metrics[market].append({
                'train_start': train_start.isoformat(),
                'test_start': test_start.isoformat(),
                'test_end': test_end.isoformat(),
                'rows': len(y_true),
                'accuracy': round(float(accuracy_score(y_true, y_pred)), 4),
                'brier': round(float(brier_score_loss(y_true, probs)), 4),
                'logloss': round(float(log_loss(y_true, probs)), 4),
                'recommended_bets': len(bet_idx),
                'roi_per_bet': round(float(units_profit / len(bet_idx)), 4) if bet_idx else None,
                'avg_edge': round(float(sum(edges) / len(edges)), 4) if edges else None,
                'bins': _calibration_bins(list(zip(probs, y_true)), bins=bins),
            })

    def _aggregate(rows: list[dict]) -> dict:
        if not rows:
            return {'error': 'no_fold_metrics'}
        metric_keys = ('accuracy', 'brier', 'logloss')
        avg = {f'avg_{k}': round(sum(float(r[k]) for r in rows) / len(rows), 4) for k in metric_keys}
        roi_values = [float(r['roi_per_bet']) for r in rows if r.get('roi_per_bet') is not None]
        avg['avg_roi_per_bet'] = round(sum(roi_values) / len(roi_values), 4) if roi_values else None
        avg['folds'] = len(rows)
        return avg

    return {
        'window_days': int(days),
        'train_days': int(train_days),
        'test_days': int(test_days),
        'step_days': int(step_days),
        'rows_scanned': len(snaps),
        'policy_used': policy,
        'markets': {
            'moneyline': {
                'summary': _aggregate(market_fold_metrics['moneyline']),
                'folds': market_fold_metrics['moneyline'],
            },
            'total_ou': {
                'summary': _aggregate(market_fold_metrics['total_ou']),
                'folds': market_fold_metrics['total_ou'],
            },
        },
    }


def run_market_governance(
    days: int = 180,
    bins: int = 5,
    min_bets: int = 20,
    drift_threshold: float = 0.05,
    train_days: int = 60,
    test_days: int = 14,
    step_days: int = 14,
    apply: bool = True,
) -> dict:
    """Run threshold tuning, guard checks, and walk-forward validation."""
    tune = tune_market_thresholds(days=days, bins=bins, min_bets=min_bets, apply=apply)
    guard = guard_market_recommendations(
        days=days,
        bins=bins,
        drift_threshold=drift_threshold,
        min_bets=min_bets,
        apply=apply,
    )
    walkforward = walkforward_market_report(
        days=days,
        train_days=train_days,
        test_days=test_days,
        step_days=step_days,
        bins=bins,
    )
    return {
        'tune': tune,
        'guard': guard,
        'walkforward': walkforward,
    }
