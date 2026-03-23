# ML Retrain Runbook — Edge Tracker
**Stack:** XGBoost · scikit-learn · S3 (model artifacts) · Neon PostgreSQL
**Last verified:** 2026-03-22
**Source configs:** `app/services/ml_model.py`, `app/services/ml_feature_builder.py`
**Est. total time:** 15–20 min (local retrain + S3 reactivation)

---

## How Retraining Works

- **Production:** Railway scheduler runs `flask retrain` at 10:30 AM ET daily.
  The guardrail skips retrain if the active model is < 7 days old OR no new `PlayerGameLog` rows exist since last train.
- **Local retrain:** Use only when the Railway scheduler is unavailable or you need a forced retrain with fresh hyperparameters.

**Never run `railway run flask retrain --force`** — Railway's 10-min timeout kills it mid-way, leaving S3 artifacts without a corresponding `ModelMetadata` record.

---

## Production Auto-Retrain (normal path)

No action needed. Verify via:
```bash
railway logs | grep -i "retrain\|model\|train"
```
✅ Expected: `Retrain complete. Models saved to S3.` by 10:45 AM ET.

Or via the health-report command:
```bash
source .venv/bin/activate && export $(grep -v '^#' .env | xargs)
flask health-report
```
✅ Expected: `=== Active ML Models ===` section shows models < 7 days old, no `<-- STALE` flags.

---

## Forced Local Retrain

Use when:
- Production model is STALE (> 14 days per health-report)
- Major feature engineering change deployed (update `FEATURE_KEYS` in `ml_feature_builder.py`)
- Significant calibration drift detected (avg_err > 2.0 in health-report)

### Step 1 — Confirm prerequisites (2 min)
```bash
source .venv/bin/activate && export $(grep -v '^#' .env | xargs)
# Verify Neon connection
python3 -c "from app import create_app, db; app = create_app(); \
  ctx = app.app_context(); ctx.push(); \
  print('PlayerGameLog rows:', db.session.execute(db.text('SELECT count(*) FROM player_game_log')).scalar())"
```
✅ Expected: Row count > 500 (not enough rows = retrain won't help).

### Step 2 — Run retrain (8–12 min)
```bash
source .venv/bin/activate && export $(grep -v '^#' .env | xargs)
flask retrain --force
```
✅ Expected output sequence:
```
Starting retrain (forced)...
Training player_points... val_mae=1.23
Training player_rebounds... val_mae=0.87
...
Models saved to S3: s3://bucket/models/xgb_player_points_v<timestamp>.json
ModelMetadata records created.
```
⚠️ Neon drops SSL after ~5 min idle. If you see `SSL connection closed`, the script handles `db.session.remove(); db.engine.dispose()` internally — it should retry.

### Step 3 — Verify local artifacts created (1 min)
```bash
ls -la app/ml_models/*.json | head -10
```
✅ Expected: Fresh `.json` files with today's timestamp.

### Step 4 — Reactivate S3 entries for Railway (3 min)

Local retrain stores artifacts with local paths (`app/ml_models/...`), which Railway can't access. Reactivate the S3 entries:
```bash
source .venv/bin/activate && export $(grep -v '^#' .env | xargs)
python3 << 'PYEOF'
from app import create_app, db
from app.models import ModelMetadata

app = create_app()
with app.app_context():
    # Deactivate local-path entries (just created)
    local = ModelMetadata.query.filter(
        ModelMetadata.is_active == True,
        ModelMetadata.file_path.like('app/ml_models/%')
    ).all()
    for m in local:
        m.is_active = False
        print(f"Deactivated local: {m.model_name} {m.version}")

    # Reactivate the most recent S3 entry per model_name
    from sqlalchemy import func
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
        m = ModelMetadata.query.filter_by(
            model_name=model_name, created_at=latest_ts
        ).first()
        if m:
            m.is_active = True
            print(f"Activated S3: {m.model_name} {m.version} {m.file_path}")

    db.session.commit()
    print("Done.")
PYEOF
```
✅ Expected: Each model_name has exactly one active S3 entry.

### Step 5 — Verify Railway will use S3 models (1 min)
```bash
flask health-report | grep -A 20 "Active ML Models"
```
✅ Expected: All models show `val_acc` > 0, age < 1 day, no `<-- STALE`.

---

## After Retrain: Monitor Calibration

Over the following week, watch the health-report drift section:
```bash
flask health-report --days 7
```
Target thresholds:
- `avg_err` between -1.0 and +1.0: no action
- `avg_err` > +1.0 (`<-- WATCH`): consider bias correction
- `avg_err` > +2.0 (`<-- DRIFT`): bias correction or retrain with more data

Bias corrections live in `app/services/projection_engine.py`:
- `COMBO_PROP_BIAS_CORRECTION` — PRA and combo props
- `SINGLE_STAT_BIAS_CORRECTION` — per stat type corrections

---

## Staleness Check
| Config File | Affects Steps |
|-------------|---------------|
| `app/services/ml_feature_builder.py` | Step 2 (FEATURE_KEYS must match between train and inference) |
| `app/services/ml_model.py` | Step 2 (training logic) |
| `.env` | All steps (DB_URL, S3 credentials) |
