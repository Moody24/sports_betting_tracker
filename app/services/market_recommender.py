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


def _train_one(model_name: str, X: list[list[float]], y: list[int], min_samples: int) -> dict:
    if len(X) < min_samples:
        return {'error': f'insufficient_samples:{len(X)}'}
    if len(set(y)) < 2:
        return {'error': 'single_class_target'}

    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import accuracy_score, log_loss
    from sklearn.model_selection import train_test_split
    import joblib

    X_train, X_val, y_train, y_val = train_test_split(
        X, y, test_size=0.3, random_state=42, stratify=y,
    )
    model = LogisticRegression(max_iter=400, class_weight='balanced')
    model.fit(X_train, y_train)
    probs = model.predict_proba(X_val)[:, 1]
    pred = (probs >= 0.5).astype(int)
    acc = float(accuracy_score(y_val, pred))
    ll = float(log_loss(y_val, probs))

    os.makedirs(MODEL_DIR, exist_ok=True)
    today = date_type.today().isoformat()
    filename = f'{model_name}_{today}.pkl'
    filepath = os.path.join(MODEL_DIR, filename)
    joblib.dump(model, filepath)
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
        }),
    )
    db.session.add(meta)
    db.session.commit()
    return {'accuracy': round(acc, 4), 'logloss': round(ll, 4), 'version': meta.version}


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

    X_ml, y_ml = [], []
    X_tot, y_tot = [], []
    for s in snaps:
        total_score = (s.home_score or 0) + (s.away_score or 0)
        if s.home_score != s.away_score:
            X_ml.append(_features_for_snapshot(s))
            y_ml.append(1 if (s.home_score or 0) > (s.away_score or 0) else 0)
        if total_score != float(s.over_under_line or 0):
            X_tot.append(_features_for_snapshot(s))
            y_tot.append(1 if total_score > float(s.over_under_line or 0) else 0)

    return {
        'moneyline': _train_one(MODEL_NAME_ML, X_ml, y_ml, min_samples=min_samples),
        'total_ou': _train_one(MODEL_NAME_TOTAL, X_tot, y_tot, min_samples=min_samples),
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
            p_home = float(ml_model.predict_proba(row)[0][1])
            p_away = 1.0 - p_home
            edge_home = p_home - _implied_prob(int(ml_h))
            edge_away = p_away - _implied_prob(int(ml_a))
            side = 'home' if edge_home >= edge_away else 'away'
            edge = edge_home if side == 'home' else edge_away
            entry['moneyline'] = {
                'side': side,
                'edge': round(edge, 3),
                'confidence': round(max(p_home, p_away), 3),
                'action': 'bet' if edge >= 0.03 else 'pass',
                'model_version': ml_meta.version if ml_meta else None,
            }
        if tot_model:
            p_over = float(tot_model.predict_proba(row)[0][1])
            if p_over >= 0.56:
                action = 'bet'
                side = 'over'
            elif p_over <= 0.44:
                action = 'bet'
                side = 'under'
            else:
                action = 'pass'
                side = 'over' if p_over >= 0.5 else 'under'
            entry['total'] = {
                'side': side,
                'confidence': round(max(p_over, 1.0 - p_over), 3),
                'action': action,
                'edge': round(abs(p_over - 0.5), 3),
                'model_version': tot_meta.version if tot_meta else None,
            }
        out[espn_id] = entry
    return out
