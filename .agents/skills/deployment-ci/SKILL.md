---
name: deployment-ci
description: "INVOKE when working on CI/CD, GitHub Actions, Railway deployment, pushing to main, coverage gates, gunicorn config, or Docker setup for Edge Tracker."
---

## CI (GitHub Actions)
- Runs on push/PR to `main`
- Python 3.11; runtime and development requirements are installed separately
- `SECRET_KEY=ci-test-secret-key-not-for-production`
- Coverage gate: `python -m coverage report --include="app/*" --fail-under=80`
- Current displayed coverage: 80%
- Lint/security job: Ruff, Bandit, detect-secrets on tracked files, a redacted
  high-confidence Git-history scan, and pip-audit for runtime dependencies

## Deployment (Currently Inactive)
Railway deployment is disconnected. Config files remain in repo for restoration:
- `railway.toml`, `gunicorn.conf.py`, `docker-entrypoint.sh`
- Former live URL: `https://sportsbettingtracker-production.up.railway.app`
- Former Railway project: `shimmering-youth` · Service: `sports_betting_tracker`
- Surviving inactive runbook: `docs/deploy.md`
- Safe web baseline: one Gunicorn worker with `SCHEDULER_ENABLED=false` and
  `RATELIMIT_ENABLED=true`. Multiple workers require a shared limiter URI and
  otherwise fail application startup in production.

## Model Storage in Production
- `MODEL_STORAGE=s3` was used when deployed (AWS now disconnected)
- Local default is `MODEL_STORAGE=local` — artifacts at `app/ml_models/*.json`

## Git Workflow
- Always `git pull --rebase origin main` before push
- Generated caches, reports, stale virtualenvs, and backup files must not appear
  in the working tree. Keep local database/model artifacts only while referenced.
