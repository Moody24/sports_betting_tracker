# Deployment Runbook — Edge Tracker
**Stack:** Flask + SQLAlchemy + XGBoost · Neon PostgreSQL · Railway
**Last verified:** 2026-03-22
**Source configs:** `railway.toml`, `docker-entrypoint.sh`, `requirements.txt`
**Est. total time:** 5–10 min (auto-deploy) · 15–20 min (with migration)

---

## How Deployments Work

Every push to `main` triggers Railway's auto-deploy pipeline:
1. Railway clones the repo and builds the container
2. `docker-entrypoint.sh` runs `flask db upgrade heads` via the Python API before gunicorn starts
3. `gunicorn.conf.py` starts the app workers

There is no manual deploy step for normal changes.

---

## Pre-Deployment Checklist

- [ ] CI is green on the commit (`github.com/<repo>/actions`)
- [ ] `ruff check .` passes locally — Railway won't catch lint errors
- [ ] If schema changed: new Alembic migration file exists and is committed
- [ ] If migration touches large table (>1M rows): plan for lock duration (see DB runbook)
- [ ] ML model artifact path: if retrained locally, S3 entries are reactivated (see Retrain runbook)

---

## Steps

### Step 1 — Push to main (< 1 min)
```bash
git push origin main
```
✅ Expected: GitHub Actions CI starts within 30 seconds.

### Step 2 — Monitor CI (3–5 min)
```bash
# Watch CI status
gh run list --limit 5
gh run watch  # streams live output
```
✅ Expected: All jobs green (lint → security → test → coverage).

### Step 3 — Monitor Railway deploy (3–5 min)
```bash
railway login --browserless   # if not already logged in
railway link                  # select: shimmering-youth → production
railway logs --tail           # stream live logs
```
✅ Expected output in logs:
```
Running DB migration...
INFO  [alembic.runtime.migration] Running upgrade ...
Gunicorn starting...
Listening at: http://0.0.0.0:$PORT
```

### Step 4 — Smoke test (2 min)
```bash
# Health endpoint
curl -sf https://sportsbettingtracker-production.up.railway.app/health | python3 -m json.tool

# Check app loads (follow redirects)
curl -sI https://sportsbettingtracker-production.up.railway.app/ | grep HTTP
```
✅ Expected: `{"status": "healthy"}` · HTTP 200 or 302 on root.

### Step 5 — Verify scheduler started (1 min)
```bash
railway logs | grep -i "scheduler\|APScheduler\|job"
```
✅ Expected: `Scheduler started` within 60 seconds of deploy.

---

## Rollback

Railway does not support one-click rollback via CLI. Options:

**Option A — Revert commit and redeploy (preferred)**
```bash
git revert HEAD --no-edit
git push origin main
```
✅ Expected: New deploy starts automatically with reverted code.

**Option B — Force-push previous commit (use only if revert is impractical)**
```bash
# WARNING: force-push — confirm with team first
git push origin <previous-sha>:main --force
```

**Option C — Database rollback (only if migration was applied)**
```bash
source .venv/bin/activate && export $(grep -v '^#' .env | xargs)
flask --app run.py db downgrade -1
```
⚠️ Confirm data impact before running. `downgrade -1` goes one step back.

---

## Staleness Check
| Config File | Affects Steps |
|-------------|---------------|
| `railway.toml` | Step 3 (build config) |
| `docker-entrypoint.sh` | Step 3 (migration + startup) |
| `gunicorn.conf.py` | Step 3 (worker count, timeout) |
| `.github/workflows/ci.yml` | Step 2 (CI checks) |

Run `git log -1 --format=%ci -- railway.toml docker-entrypoint.sh` to check freshness.

---

## Escalation
- **Build fails:** Check CI logs → fix lint/test → push again
- **Migration fails at startup:** Check `railway logs` for Alembic error → apply fix → push
- **Health check returns 500:** Check Railway logs for Python traceback → check env vars are set
- **Scheduler not starting:** Check `_is_non_server_invocation()` in `app/__init__.py`
