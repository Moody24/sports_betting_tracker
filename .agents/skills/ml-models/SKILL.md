---
name: ml-models
description: Use for Edge Tracker projection, feature, pick-quality, calibration, retraining or model-artifact changes.
---

# ML and model contracts

Read [the system contract](../../../docs/architecture/system-contract.md),
[retraining guide](../../../docs/runbooks/retrain.md) and
[validation gates](../../../docs/runbooks/ml-validation.md) for the affected
model. Training and inference must use the same ordered Model 1 features from
`app/services/ml_feature_builder.py::FEATURE_KEYS`. Model 2 owns separate
features in `app/services/pick_quality_model.py::PICK_FEATURES`.
Inspect `app/services/ml_model.py`, `projection_engine.py` and
`value_detector.py` for current loading, scoring and calibration behavior.

Treat provider keys, training-row counts, calibration values and artifact
availability as environment-dependent. Do not assert that `ODDS_API_KEY` is
active. Preserve temporal evaluation, real-line coverage, calibration and
economic gates before promoting a model. Run a focused validation on
synthetic or approved isolated data. Do not force retraining by default;
decide from the current source and runbook, and obtain the appropriate
separate authorization before running real training or promotion.
