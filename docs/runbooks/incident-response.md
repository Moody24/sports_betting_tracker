# Incident Response Runbook — Edge Tracker (Railway — INACTIVE)

> ⚠️ **This runbook was written for the Railway + Neon production stack, which is currently inactive.**
> Railway-specific commands (`railway logs`, `railway link`) will not work locally.
> For local issues, use `flask health-report`, `flask pollution-report`, and `sqlite3 instance/app.db`.
> This file is preserved for when external deployment is restored.

---

# Incident Response Runbook — Edge Tracker
**Stack:** Flask · Railway · Neon PostgreSQL · APScheduler
**Last verified:** 2026-03-22
**Est. total time:** P1: 15–30 min · P2: 30–90 min

---

## Severity Levels

| Level | Condition | Response |
|-------|-----------|----------|
| P1 | App completely down · health check fails · 5xx on all requests | Fix immediately |
| P2 | Specific feature broken · scheduler stopped · ML projections wrong | Fix within hours |
| P3 | Cosmetic issue · single stat drifting · stale model | Fix in business hours |

---

## Phase 1 — Triage (2–5 min)

### Is the app up?
```bash
curl -sw "%{http_code}" https://sportsbettingtracker-production.up.railway.app/health -o /dev/null
```
- `200` = app is up → go to Phase 2 for partial issues
- `5xx` or timeout → **P1** — proceed below

### Check Railway logs (last 15 min)
```bash
railway link   # select: shimmering-youth → production
railway logs --tail | grep -i "error\|exception\|traceback\|5[0-9][0-9]"
```

### Did something deploy recently?
```bash
gh run list --limit 5 --repo <owner>/<repo>
```
If a deploy happened in the last 30 min: that's the most likely cause → rollback (see Deployment runbook).

---

## Phase 2 — Diagnose

### App returning 500s

```bash
railway logs | grep -i "error\|traceback" | tail -50
```

Common causes:
- **Missing env var** — look for `KeyError` or `RuntimeError: SECRET_KEY not set`
- **DB connection failed** — look for `psycopg2.OperationalError` or `SSL connection`
- **Import error after dependency change** — look for `ImportError` or `ModuleNotFoundError`

### Scheduler stopped

```bash
flask health-report --job-days 1
```
✅ Expected: All 17 job types show recent runs. If a job shows 0 runs in the past day:
```bash
railway logs | grep -i "scheduler\|APScheduler\|job_name"
```
Look for `_is_non_server_invocation()` returning True unexpectedly, or an unhandled exception during job startup.

### Neon DB issues

```bash
source .venv/bin/activate && export $(grep -v '^#' .env | xargs)
psql $DATABASE_URL -c "SELECT now();"  # basic connectivity
```

Check active connections:
```bash
psql $DATABASE_URL << 'SQL'
SELECT count(*), state
FROM pg_stat_activity
GROUP BY state;
SQL
```
✅ Expected: `active` connections < 10 (Neon serverless auto-scales but has connection limits per plan).

Check for long-running queries:
```bash
psql $DATABASE_URL << 'SQL'
SELECT pid, now() - query_start AS duration, state, query
FROM pg_stat_activity
WHERE state = 'active'
  AND now() - query_start > interval '10 seconds'
ORDER BY duration DESC;
SQL
```
Kill a runaway query:
```bash
psql $DATABASE_URL -c "SELECT pg_terminate_backend(<pid>);"
```

### ML projections returning wrong values

1. Check model age:
```bash
flask health-report | grep -A 20 "Active ML Models"
```
If models are STALE (> 14 days): follow Retrain runbook.

2. Check calibration drift:
```bash
flask health-report --days 7
```
If `avg_err` > 2.0 on any stat type: update bias corrections in `projection_engine.py` (see calibration notes in CLAUDE.md).

3. Check for FEATURE_KEYS mismatch (causes silent prediction errors):
```bash
# Verify training and inference use the same features
grep -n "FEATURE_KEYS" app/services/ml_feature_builder.py
```
The list must be identical between training and inference. If you see a mismatch after a deploy: retrain immediately.

---

## Phase 3 — Mitigate

### Rollback a bad deploy
See Deployment runbook → Rollback section.

### Restart the scheduler without a full redeploy
Schedulers restart on every Railway deploy. To force a restart:
```bash
git commit --allow-empty -m "chore: force restart to recover scheduler"
git push origin main
```

### Disable the scheduler temporarily
Set `SCHEDULER_ENABLED=false` as a Railway environment variable, then redeploy. This lets the app serve requests while you debug scheduler issues.

---

## Phase 4 — Resolve & Follow-Up

After the incident is resolved, within 24 hours:
1. Write a short timeline in `docs/postmortems/YYYY-MM-DD-<title>.md`
2. Identify root cause (5-Whys)
3. Update this runbook if a step was missing or wrong
4. Add monitoring / alert if the issue could have been caught earlier (e.g. add a health-report check to the scheduler)

---

## Quick Reference

| Signal | Likely cause | First step |
|--------|-------------|------------|
| `SECRET_KEY not set` | Missing env var in Railway | Add env var in Railway dashboard |
| `psycopg2.OperationalError` | Neon SSL timeout or connection limit | Check pg_stat_activity, check Neon dashboard |
| `ModuleNotFoundError` | Dependency not in requirements.txt | Add dep + push |
| `500` after deploy | Bad migration or code bug | Check railway logs → rollback |
| Scheduler silent | `_is_non_server_invocation()` guard fired | Check Railway logs for `Skipping scheduler` |
| STALE models | Railway retrain job failed | Check health-report → follow retrain runbook |
| Projection drift > 2.0 | Model needs retrain or bias correction | Run `flask health-report --days 14` → decide |
