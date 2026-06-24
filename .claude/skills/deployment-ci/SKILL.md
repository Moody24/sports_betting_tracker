---
name: deployment-ci
description: "INVOKE when working on CI/CD, GitHub Actions, Railway deployment, pushing to main, coverage gates, gunicorn config, or Docker setup for Edge Tracker."
---

## CI (GitHub Actions)
- Runs on push/PR to `main`
- Python 3.11, `pip install -r requirements.txt`
- `SECRET_KEY=ci-test-secret-key-not-for-production`
- Coverage gate: `python -m coverage report --include="app/*" --fail-under=80`
- Current actual coverage: ~75% — target 80% tracked in backlog

## Deployment (Currently Inactive)
Railway deployment is disconnected. Config files remain in repo for restoration:
- `railway.toml`, `gunicorn.conf.py`, `docker-entrypoint.sh`
- Former live URL: `https://sportsbettingtracker-production.up.railway.app`
- Former Railway project: `shimmering-youth` · Service: `sports_betting_tracker`
- Runbooks: `docs/runbooks/deployment.md`, `docs/deploy.md`

## Model Storage in Production
- `MODEL_STORAGE=s3` was used when deployed (AWS now disconnected)
- Local default is `MODEL_STORAGE=local` — artifacts at `app/ml_models/*.json`

## Git Workflow
- Always `git pull --rebase origin main` before push
- Exclude from commits (untracked noise, not gitignored): `instance.bak/`, `tests/helpers.py.backup`
