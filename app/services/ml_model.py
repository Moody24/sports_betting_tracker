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
from app.config_display import PROP_STAT_KEY
from app.models import ModelMetadata, PlayerGameLog, TeamDefenseSnapshot, GameSnapshot, OddsSnapshot
from app.services.model_storage import materialize_model_artifact, persist_model_artifact
from app.services.ml_feature_builder import build_ml_features_from_history, build_team_game_aggregates

logger = logging.getLogger(__name__)

# Directory for saved model files
MODEL_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'ml_models')

# Stat types we build models for
STAT_TYPES = list(PROP_STAT_KEY.keys())

# Re-export from centralized config
STAT_KEY_MAP = PROP_STAT_KEY

# Minimum training samples required
MIN_TRAIN_SAMPLES = 500


def _ensure_model_dir():
    """Create the ml_models directory if it doesn't exist."""
    os.makedirs(MODEL_DIR, exist_ok=True)


def _build_defense_lookup() -> dict:
    """Pre-fetch TeamDefenseSnapshot into {team_abbr: {field: value}} dict.

    Only the most-recent snapshot per team is kept.  Falls back to team_name
    for teams that lack an abbreviation.
    """
    rows = TeamDefenseSnapshot.query.order_by(TeamDefenseSnapshot.snapshot_date.desc()).all()
    lookup: dict = {}
    for row in rows:
        key = (row.team_abbr or '').strip().upper() or row.team_name
        if not key or key in lookup:
            continue  # keep the first (most recent) snapshot per team
        lookup[key] = {
            'def_rating':  row.def_rating,
            'pace':        row.pace,
            'opp_pts_pg':  row.opp_pts_pg,
            'opp_reb_pg':  row.opp_reb_pg,
            'opp_ast_pg':  row.opp_ast_pg,
            'opp_3pm_pg':  row.opp_3pm_pg,
            'opp_stl_pg':  row.opp_stl_pg,
            'opp_blk_pg':  row.opp_blk_pg,
            # store full name so game-total lookup can cross-reference
            '_team_name':  row.team_name,
        }
    return lookup


def _build_game_total_lookup(defense_lookup: dict) -> dict:
    """Pre-fetch GameSnapshot O/U totals into {(game_date, team_abbr): ou_line}.

    Uses defense_lookup to map team full-names → abbreviations for the key.
    """
    # Build reverse map: normalised team name → abbr
    name_to_abbr: dict = {}
    for abbr, info in defense_lookup.items():
        tname = (info.get('_team_name') or '').strip().lower()
        if tname:
            name_to_abbr[tname] = abbr

    rows = GameSnapshot.query.filter(GameSnapshot.over_under_line.isnot(None)).all()
    lookup: dict = {}
    for row in rows:
        ou = row.over_under_line
        if not ou:
            continue
        home = (row.home_team or '').strip().lower()
        away = (row.away_team or '').strip().lower()
        gdate = row.game_date
        for tname in (home, away):
            abbr = name_to_abbr.get(tname)
            if abbr and gdate:
                lookup[(gdate, abbr)] = float(ou)
    return lookup


def _build_odds_snapshot_lookup(stat_type: str) -> dict:
    """Build line-movement lookup from OddsSnapshot for historical training rows.

    Returns {(player_name_lower, stat_type, game_date): line_delta} where
    line_delta = last_line - first_line for that player/market/date.
    Only dates with 2+ snapshots (i.e. a line actually moved) produce entries.
    """
    rows = (
        OddsSnapshot.query
        .filter(OddsSnapshot.market == stat_type)
        .filter(OddsSnapshot.line.isnot(None))
        .order_by(OddsSnapshot.game_date, OddsSnapshot.player_name, OddsSnapshot.snapped_at)
        .all()
    )
    # Group by (player_name_lower, game_date) and track first/last line seen.
    groups: dict = {}
    for row in rows:
        key = ((row.player_name or '').strip().lower(), row.game_date)
        if key not in groups:
            groups[key] = {'first': row.line, 'last': row.line}
        else:
            groups[key]['last'] = row.line

    lookup: dict = {}
    for (player_lower, gdate), vals in groups.items():
        delta = float(vals['last'] or 0.0) - float(vals['first'] or 0.0)
        if delta != 0.0:  # only store when line actually moved
            lookup[(player_lower, stat_type, gdate)] = delta
    return lookup


def _check_training_data_quality(all_logs: list) -> dict:
    """Pre-training data quality gate.

    Returns a dict with keys ``passed`` (bool) and ``issues`` (list of str).
    Training should be skipped when ``passed`` is False.
    """
    issues = []
    total = len(all_logs)

    null_pts = sum(1 for lg in all_logs if lg.pts is None)
    null_min = sum(1 for lg in all_logs if lg.minutes is None)
    bad_pts = sum(1 for lg in all_logs if lg.pts is not None and (lg.pts < 0 or lg.pts > 100))
    bad_min = sum(1 for lg in all_logs if lg.minutes is not None and (lg.minutes < 0 or lg.minutes > 60))

    null_pts_pct = null_pts / max(total, 1)
    null_min_pct = null_min / max(total, 1)

    if null_pts_pct > 0.05:
        issues.append(f"pts null rate {null_pts_pct:.1%} exceeds 5% threshold ({null_pts}/{total})")
    if null_min_pct > 0.05:
        issues.append(f"minutes null rate {null_min_pct:.1%} exceeds 5% threshold ({null_min}/{total})")
    if bad_pts > 0:
        issues.append(f"{bad_pts} rows with pts outside [0, 100]")
    if bad_min > 0:
        issues.append(f"{bad_min} rows with minutes outside [0, 60]")

    passed = len(issues) == 0
    if issues:
        logger.warning("Training data quality gate failed: %s", "; ".join(issues))

    return {"passed": passed, "issues": issues}


def check_defense_snapshot_staleness() -> dict:
    """Check if TeamDefenseSnapshot data is stale (> 7 days old).

    Returns dict with keys ``stale`` (bool), ``days_old`` (int or None),
    ``latest_date`` (date or None).
    """
    from datetime import date as date_type
    from zoneinfo import ZoneInfo

    latest = (
        TeamDefenseSnapshot.query
        .order_by(TeamDefenseSnapshot.snapshot_date.desc())
        .with_entities(TeamDefenseSnapshot.snapshot_date)
        .first()
    )
    if latest is None:
        return {"stale": True, "days_old": None, "latest_date": None}

    today = datetime.now(ZoneInfo("America/New_York")).date()
    days_old = (today - latest.snapshot_date).days
    stale = days_old > 7
    if stale:
        logger.warning(
            "TeamDefenseSnapshot is stale: latest=%s (%d days old)",
            latest.snapshot_date, days_old,
        )
    return {"stale": stale, "days_old": days_old, "latest_date": latest.snapshot_date}


def _build_training_rows(stat_type: str):
    """Build dated training rows for walk-forward validation."""
    stat_key = STAT_KEY_MAP.get(stat_type, 'pts')

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
        return []

    quality = _check_training_data_quality(all_logs)
    if not quality["passed"]:
        logger.warning(
            "Skipping %s model training due to data quality issues: %s",
            stat_type, quality["issues"],
        )
        return []

    player_logs = {}
    for log in all_logs:
        player_logs.setdefault(log.player_id, []).append(log)

    team_totals, team_counts = build_team_game_aggregates(all_logs)

    # Phase 1.1: pre-build context lookups once to avoid per-row DB queries
    defense_lookup = _build_defense_lookup()
    game_total_lookup = _build_game_total_lookup(defense_lookup)
    # Phase 2: line movement — only available for recent rows; older rows get 0.0
    odds_snapshot_lookup = _build_odds_snapshot_lookup(stat_type)

    rows = []

    for pid, logs in player_logs.items():
        logs = sorted(logs, key=lambda lg: ((lg.game_date is None), lg.game_date))
        if len(logs) < 10:
            continue

        for i in range(10, len(logs)):
            prior = logs[:i]
            current = logs[i]
            target = float(getattr(current, stat_key, 0.0) or 0.0)

            # Phase 1.1 context for this specific game row
            team_abbr = (current.team_abbr or '').strip().upper()
            ou_line = game_total_lookup.get((current.game_date, team_abbr), 0.0)

            player_lower = (current.player_name or '').strip().lower()
            line_delta = odds_snapshot_lookup.get(
                (player_lower, stat_type, current.game_date), 0.0
            )

            features = build_ml_features_from_history(
                prior_logs=prior,
                current_is_home=(current.home_away or '').lower() == 'home',
                stat_key=stat_key,
                team_totals=team_totals,
                team_counts=team_counts,
                current_game_date=current.game_date,
                current_matchup=current.matchup or '',
                game_total_line=ou_line,
                defense_lookup=defense_lookup,
                line_delta=line_delta,
            )
            rows.append((current.game_date, str(pid), features, target))

    rows.sort(key=lambda r: ((r[0] is None), r[0], r[1]))
    return rows


def _build_training_data(stat_type: str):
    """Build globally time-ordered training data for a stat model."""
    rows = _build_training_rows(stat_type)
    if not rows:
        return None, None
    features_list = [r[2] for r in rows]
    targets = [r[3] for r in rows]
    return features_list, targets


def train_model(stat_type: str) -> dict:
    """Train an XGBoost model for a specific stat type.

    Returns a dict with training results metadata.
    """
    try:
        from xgboost import XGBRegressor
        from sklearn.metrics import mean_absolute_error
        from sklearn.model_selection import TimeSeriesSplit
        import numpy as np
    except ImportError:
        logger.error("xgboost or scikit-learn not installed")
        return {'error': 'Missing ML dependencies'}

    player_game_log_rows = PlayerGameLog.query.count()
    training_rows = _build_training_rows(stat_type)
    if not training_rows:
        return {'error': 'Insufficient training data', 'stat_type': stat_type}

    feature_names = list(training_rows[0][2].keys())
    X = np.array([[row[2][k] for k in feature_names] for row in training_rows])
    y = np.array([row[3] for row in training_rows])

    xgb_params = dict(
        n_estimators=500,
        max_depth=5,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        reg_alpha=0.1,
        reg_lambda=1.0,
        random_state=42,
        early_stopping_rounds=25,
    )

    # TimeSeriesSplit CV to estimate MAE variance across time folds
    tscv = TimeSeriesSplit(n_splits=3)
    cv_maes = []
    for cv_train_idx, cv_val_idx in tscv.split(X):
        cv_model = XGBRegressor(**xgb_params)
        cv_model.fit(
            X[cv_train_idx], y[cv_train_idx],
            eval_set=[(X[cv_val_idx], y[cv_val_idx])],
            verbose=False,
        )
        cv_preds = cv_model.predict(X[cv_val_idx])
        cv_maes.append(mean_absolute_error(y[cv_val_idx], cv_preds))
    cv_mean_mae = float(np.mean(cv_maes))
    cv_std_mae = float(np.std(cv_maes))

    # Final model: date-based walk-forward split
    split_method = 'date_cutoff'
    cutoff_date = None
    unique_dates = sorted({row[0] for row in training_rows if row[0] is not None})

    train_idx = []
    val_idx = []
    if len(unique_dates) >= 2:
        cutoff_idx = int(len(unique_dates) * 0.8) - 1
        cutoff_idx = max(0, min(cutoff_idx, len(unique_dates) - 2))
        cutoff_date = unique_dates[cutoff_idx]
        for idx, row in enumerate(training_rows):
            row_date = row[0]
            if row_date is not None and row_date <= cutoff_date:
                train_idx.append(idx)
            else:
                val_idx.append(idx)

    if not train_idx or len(val_idx) < 1:
        split_method = 'index_fallback'
        split_idx = int(len(X) * 0.8)
        split_idx = min(max(split_idx, 1), len(X) - 1)
        train_idx = list(range(split_idx))
        val_idx = list(range(split_idx, len(X)))

    if not train_idx or not val_idx:
        return {'error': 'Insufficient validation data', 'stat_type': stat_type}

    X_train, X_val = X[train_idx], X[val_idx]
    y_train, y_val = y[train_idx], y[val_idx]

    model = XGBRegressor(**xgb_params)

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
    artifact_path = persist_model_artifact(filepath, filename)

    # Store metadata
    version = f"{stat_type}_{today}"
    # Reconnect before DB writes — training can take 5-10 min and Neon/serverless
    # Postgres drops idle SSL connections; remove the stale connection from the pool.
    try:
        db.session.remove()
        db.engine.dispose()
    except Exception:
        pass
    # Deactivate previous models for this stat type
    ModelMetadata.query.filter_by(
        model_name=f"projection_{stat_type}", is_active=True
    ).update({'is_active': False})

    meta = ModelMetadata(
        model_name=f"projection_{stat_type}",
        model_type='xgboost_regressor',
        version=version,
        file_path=artifact_path,
        training_date=datetime.now(timezone.utc),
        training_samples=len(X_train),
        val_mae=round(mae, 3),
        is_active=True,
        metadata_json=json.dumps({
            'feature_names': feature_names,
            'val_samples': len(X_val),
            'train_samples': len(X_train),
            'player_game_log_rows': player_game_log_rows,
            'split_method': split_method,
            'cutoff_date': cutoff_date.isoformat() if cutoff_date else None,
            'cv_mean_mae': round(cv_mean_mae, 3),
            'cv_std_mae': round(cv_std_mae, 3),
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
        'model_path': artifact_path,
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

    if not meta:
        return None, None
    local_model_path = materialize_model_artifact(meta.file_path)
    if not local_model_path:
        return None, None

    model = XGBRegressor()
    model.load_model(local_model_path)

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
    weighted average projection).  Logs a warning when TeamDefenseSnapshot
    data is stale (> 7 days) so operators are alerted before inference quality
    degrades silently.
    """
    staleness = check_defense_snapshot_staleness()
    if staleness["stale"]:
        latest = staleness.get("latest_date")
        days_old = staleness.get("days_old")
        if days_old is not None:
            logger.warning(
                "Inference with stale TeamDefenseSnapshot: %s (%d days old) — "
                "defensive context features may be outdated",
                latest, days_old,
            )
        else:
            logger.warning("No TeamDefenseSnapshot rows found — defensive context features missing")

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
