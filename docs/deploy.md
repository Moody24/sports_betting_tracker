# Deployment Runbook (Railway + Neon)

This runbook is for deploying the app on Railway with Neon Postgres.

## Environment Variables

Set these in Railway service variables:

- `SECRET_KEY`: Required for Flask app startup and CLI commands.
- `DATABASE_URL`: Neon Postgres connection string.
- `FLASK_ENV`: Usually `production`.
- `AUTO_DB_UPGRADE`: Optional. Keep `false` unless you intentionally want boot-time migrations.

Optional model-storage variables (only if using S3 model artifacts):

- `MODEL_STORAGE=s3`
- `S3_MODEL_BUCKET`
- `S3_MODEL_PREFIX` (example: `models/`)
- `AWS_REGION`
- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`

Neon URL notes:

- Use `sslmode=require`.
- `postgres://` URLs are rewritten by the app to `postgresql://` for SQLAlchemy compatibility.

## Deployment Checklist

1. Confirm migrations are committed and reviewed.
2. Validate all tests locally before push:
   ```bash
   .venv/bin/pytest -q
   ```
3. Push branch and wait for CI to finish.
4. Deploy to Railway only after CI is green (required gate).
5. Deploy to Railway staging environment first.
6. Run migrations on staging:
   ```bash
   flask --app run.py db upgrade heads
   ```
7. Smoke test key pages (`/`, `/bets`, `/bets/nba_today`, auth flow).
8. Promote to production.
9. Run migrations on production (if not using controlled boot-time upgrade).

## Testing Safety Rules

- Never run tests against production Neon DB.
- Use local or dedicated test DB for `pytest`.
- Keep test config isolated from Railway runtime environment variables.

## CLI Runbook (Safe Operations)

Run CLI commands in staging before production when they mutate data.

Read-only / diagnostic commands:

- `flask --app run.py pollution_report`
- `flask --app run.py evaluate-calibration --days 14`

Mutating commands (run with extra care):

- `flask --app run.py pollution_report --fix`
- `flask --app run.py pollution_report --fix --retrain-after`
- `flask --app run.py retrain --force`

Recommended sequence for cleanup/retrain:

1. `flask --app run.py pollution_report`
2. Validate reported counts and sample records.
3. `flask --app run.py pollution_report --fix` (staging first).
4. Re-check with `pollution_report`.
5. Run targeted smoke tests and model-dependent pages.
6. Optionally run `--retrain-after` once cleanup is validated.

## Rollback Notes

- Keep a recent Neon backup/snapshot strategy.
- If a deploy fails after migration, fix-forward is preferred. Only rollback DB schema when you have a validated reverse migration path.
- For app-only regressions (no schema issue), redeploy previous working image.

## Phase 5 (Web + Scheduler Split) Verification

After deploying both services, run the production verification script:

```bash
./scripts/phase5_verify_prod.sh
```

Optional: pass a custom web URL.

```bash
./scripts/phase5_verify_prod.sh https://your-web-domain
```

What this checks:

1. Web health/readiness endpoints respond and DB/model probes are healthy.
2. Service guard rails remain correct:
   - `sports_betting_tracker`: `SCHEDULER_ENABLED=false`
   - `scheduler`: `SCHEDULER_ENABLED=true`
3. Latest deployments for both services are visible.
4. `prod-readiness` report passes with no `FAIL`.
5. Recent scheduler `JobLog` entries are being written.
