"""Unified distributional inference for quantile and Poisson heads."""

import json
import logging
from typing import Optional

from app.models import ModelMetadata
from app.services.distribution import (
    median_from_quantiles,
    prob_over,
    prob_over_poisson,
    rectify_quantiles,
)
from app.services.distribution_calibration import apply_calibrator
from app.services.distributional_model import (
    DIST_STAT_TYPES,
    POISSON_DIST_STAT_TYPES,
    QUANTILE_ALPHAS,
)
from app.services.model_storage import materialize_model_artifact

logger = logging.getLogger(__name__)


def load_quantile_model(stat_type: str):
    """Return the active dist model and feature names, or two None values."""
    from xgboost import XGBRegressor

    meta = ModelMetadata.query.filter_by(
        model_name=f"dist_{stat_type}", is_active=True
    ).first()
    if not meta:
        return None, None
    local_path = materialize_model_artifact(meta.file_path)
    if not local_path:
        return None, None

    model = XGBRegressor()
    model.load_model(local_path)

    feature_names = None
    if meta.metadata_json:
        try:
            feature_names = json.loads(meta.metadata_json).get("feature_names")
        except (ValueError, TypeError):
            pass
    return model, feature_names


def load_calibrator(stat_type: str):
    """Return the active isotonic calibrator for the stat, or None."""
    meta = ModelMetadata.query.filter_by(
        model_name=f"dist_calibrator_{stat_type}", is_active=True
    ).first()
    if not meta:
        return None
    local_path = materialize_model_artifact(meta.file_path)
    if not local_path:
        return None
    try:
        import joblib

        return joblib.load(local_path)
    except Exception as exc:
        logger.warning("Failed to load calibrator for %s: %s", stat_type, exc)
        return None


def predict_distribution(stat_type: str, features: dict) -> Optional[dict]:
    """Predict a raw distribution, or None when no head is available."""
    import numpy as np

    if stat_type in DIST_STAT_TYPES:
        model, feature_names = load_quantile_model(stat_type)
        if model is None or feature_names is None:
            return None
        missing = [key for key in feature_names if key not in features]
        if missing:
            logger.warning(
                "Missing dist features for %s — zero-filled: %s", stat_type, missing
            )
        matrix = np.array([[features.get(key, 0) for key in feature_names]])
        raw = model.predict(matrix)[0].tolist()
        rectified = rectify_quantiles(raw)
        point = median_from_quantiles(QUANTILE_ALPHAS, rectified)
        return {
            "kind": "quantile",
            "point": point,
            "alphas": QUANTILE_ALPHAS,
            "quantile_values": rectified,
        }

    if stat_type in POISSON_DIST_STAT_TYPES:
        from app.services.ml_model import predict_stat

        lam = predict_stat(stat_type, features)
        if lam <= 0:
            return None
        return {"kind": "poisson", "point": lam, "lam": lam}

    return None


def predict_prob_over(
    stat_type: str, features: dict, line: float
) -> Optional[float]:
    """Return calibrated P(stat > line), or None when no head is active."""
    dist = predict_distribution(stat_type, features)
    if dist is None:
        return None

    if dist["kind"] == "poisson":
        raw = prob_over_poisson(line, dist["lam"])
    else:
        raw = prob_over(line, dist["alphas"], dist["quantile_values"])

    calibrator = load_calibrator(stat_type)
    if calibrator is not None:
        return apply_calibrator(calibrator, raw)
    return float(raw)
