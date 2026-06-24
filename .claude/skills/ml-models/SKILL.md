---
name: ml-models
description: "INVOKE when working on anything related to XGBoost projections, pick quality model, FEATURE_KEYS, calibration constants, auto-pick thresholds, model retraining, or model artifact storage in Edge Tracker."
---

## ML Models Architecture

**Model 1 — Projections (6 XGBoost regressors)**
- One regressor per stat type: points, rebounds, assists, 3s, steals, blocks
- `FEATURE_KEYS` in `ml_feature_builder.py` is the canonical shared feature list — must stay in sync between training and inference (currently 37 features)
- `ml_feature_builder.py` is the single source of truth for feature construction; never build features elsewhere

**Model 2 — Pick Quality (XGBoost classifier)**
- Stored as `.pkl` when calibrated, `.json` as fallback
- Input includes 2 volatility features: `minutes_volatility`, `stat_attempts_volatility`

## Calibration Constants (`projection_engine.py`)
- `COMBO_PROP_BIAS_CORRECTION`: PRA +3.2
- `SINGLE_STAT_BIAS_CORRECTION`: assists +0.5, rebounds +0.3
- Revisit blocks/steals when N ≥ 30

## Auto-Pick Thresholds (`scheduler.py`)
All env-configurable (defaults unchanged):
- `AUTO_PICK_MAX_TOTAL`
- `AUTO_PICK_MIN_EDGE_STRAIGHT` / `AUTO_PICK_MIN_EDGE_2LEG` / `AUTO_PICK_MIN_EDGE_3LEG`
- `AUTO_PICK_MIN_GAMES`
- `AUTO_PICK_CONFIDENCE_TIER`

## Model Storage
- `MODEL_STORAGE=local` (default) — artifacts at `app/ml_models/*.json` (gitignored)
- `MODEL_STORAGE=s3` was used in production; AWS is currently disconnected
- Run `flask retrain --force` to regenerate local artifacts

## Force Retrain
```bash
source .venv/bin/activate && flask retrain --force
```
- Takes ~8–12 min; requires >500 `PlayerGameLog` rows in DB
- Guardrail: skips if active model is <7 days old OR no new rows since last train
- Full guide: `docs/runbooks/retrain.md`

## Odds API (live props source)
- `ODDS_API_KEY` active — live player props and market lines work
- Endpoint: `https://api.the-odds-api.com/v4/sports/basketball_nba/...`
- Fallback: live odds pages degrade gracefully if key is missing
