# ML Retrain Runbook — Edge Tracker
**Stack:** XGBoost · scikit-learn · Local model artifacts (`app/ml_models/*.json`)
**Last verified:** 2026-06-24
**Source configs:** `app/services/ml_model.py`, `app/services/ml_feature_builder.py`
**Est. total time:** 10–15 min

---

## How Retraining Works

- Models are XGBoost regressors (Model 1: stat projections) and a classifier (Model 2: pick quality)
- Training data comes from the `player_game_log` table — you need at least 500 rows for meaningful models
- Artifacts are saved to `app/ml_models/*.json` (gitignored, local-only)
- The guardrail skips retrain if the active model is < 7 days old OR no new `PlayerGameLog` rows exist since last train

---

## Forced Local Retrain

### Step 1 — Confirm data exists (1 min)
```bash
source .venv/bin/activate
python3 << 'PYEOF'
from app import create_app, db
app = create_app()
with app.app_context():
    count = db.session.execute(db.text("SELECT count(*) FROM player_game_log")).scalar()
    print(f"PlayerGameLog rows: {count}")
    if count < 500:
        print("⚠️  Row count is low — retrain may produce unreliable models")
    else:
        print("✅ Sufficient data for retrain")
PYEOF
```

### Step 2 — Run retrain (8–12 min)
```bash
source .venv/bin/activate
flask retrain --force
```

✅ Expected output sequence:
```
Starting retrain (forced)...
Training player_points... val_mae=1.23
Training player_rebounds... val_mae=0.87
Training player_assists... val_mae=0.71
Training player_threes... val_mae=0.62
Training player_steals... val_mae=0.31
Training player_blocks... val_mae=0.28
Models saved to app/ml_models/
ModelMetadata records created.
```

### Step 3 — Verify artifacts created (1 min)
```bash
ls -la app/ml_models/*.json
```
✅ Expected: Fresh `.json` files with today's timestamp.

### Step 4 — Check model health
```bash
flask health-report
```
✅ Expected: `=== Active ML Models ===` section shows models < 1 day old, no `<-- STALE` flags.

---

## Monitor Calibration After Retrain

Over the following week, watch calibration drift:
```bash
flask health-report --days 7
```

Target thresholds:
- `avg_err` between -1.0 and +1.0: no action
- `avg_err` > +1.0 (`<-- WATCH`): consider bias correction
- `avg_err` > +2.0 (`<-- DRIFT`): bias correction or retrain with more data

Bias corrections live in `app/services/projection_engine.py`:
- `COMBO_PROP_BIAS_CORRECTION` — PRA and combo props (currently +3.2)
- `SINGLE_STAT_BIAS_CORRECTION` — per stat type corrections (assists +0.5, rebounds +0.3)

---

## Full Calibration Report
```bash
flask evaluate-calibration --days 14
```
Breaks down projection accuracy by stat type over the last N days.

---

## When to Retrain

| Trigger | Action |
|---------|--------|
| `health-report` shows STALE models (> 14 days) | `flask retrain --force` |
| `avg_err` > 2.0 on any stat type | Retrain, then update bias corrections if drift persists |
| `FEATURE_KEYS` changed in `ml_feature_builder.py` | Retrain immediately — mismatch causes silent errors |
| New season data available (significant new game logs) | Retrain for updated player baselines |

---

## Feature Engineering Notes

**CRITICAL:** `FEATURE_KEYS` in `ml_feature_builder.py` must be identical between training and inference. If you add or remove features:

1. Update `FEATURE_KEYS` in `ml_feature_builder.py`
2. Run `flask retrain --force` immediately
3. Verify `flask health-report` shows fresh models

```bash
grep -n "FEATURE_KEYS" app/services/ml_feature_builder.py
```
Currently 37 features + 2 volatility features for Model 2 (`minutes_volatility`, `stat_attempts_volatility`).

---

## Staleness Check
| Config File | Affects Steps |
|-------------|---------------|
| `app/services/ml_feature_builder.py` | Step 2 (FEATURE_KEYS must match between train and inference) |
| `app/services/ml_model.py` | Step 2 (training logic) |
| `.env` | All steps (DATABASE_URL must point to a DB with PlayerGameLog data) |

---

## Archived: S3 Model Storage (formerly used with Railway)

> When `MODEL_STORAGE=s3` was active (Railway production), models were stored in S3 and Railway
> needed S3 entries reactivated after a local retrain. This is no longer needed — `MODEL_STORAGE=local`
> is the current default. The reactivation script is preserved here for reference.

```python
# Reactivate S3 entries after local retrain (only needed if MODEL_STORAGE=s3)
from app import create_app, db
from app.models import ModelMetadata
from sqlalchemy import func

app = create_app()
with app.app_context():
    local = ModelMetadata.query.filter(
        ModelMetadata.is_active == True,
        ModelMetadata.file_path.like('app/ml_models/%')
    ).all()
    for m in local:
        m.is_active = False
        print(f"Deactivated local: {m.model_name} {m.version}")

    s3_latest = (
        db.session.query(
            ModelMetadata.model_name,
            func.max(ModelMetadata.created_at).label('latest')
        )
        .filter(ModelMetadata.file_path.like('s3://%'))
        .group_by(ModelMetadata.model_name)
        .all()
    )
    for model_name, latest_ts in s3_latest:
        m = ModelMetadata.query.filter_by(model_name=model_name, created_at=latest_ts).first()
        if m:
            m.is_active = True
            print(f"Activated S3: {m.model_name} {m.version} {m.file_path}")

    db.session.commit()
```
