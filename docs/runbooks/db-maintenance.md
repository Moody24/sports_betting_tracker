# Database Maintenance Runbook — Edge Tracker
**Stack:** Neon PostgreSQL (serverless) · Flask-Migrate (Alembic) · psycopg2-binary
**Last verified:** 2026-03-22
**Source configs:** `migrations/`, `app/models.py`
**Schedule:** Migrations: on deploy · Vacuum: Neon runs autovacuum · Manual review: monthly

---

## Connection

```bash
source .venv/bin/activate && export $(grep -v '^#' .env | xargs)
# DATABASE_URL is now in environment — verify:
echo $DATABASE_URL | cut -c1-40   # should start with postgresql://
```

For direct psql access:
```bash
psql $DATABASE_URL
```

---

## Migrations

### Apply pending migrations (deploy path)
Migrations run automatically via `docker-entrypoint.sh` on every Railway deploy. To apply manually (e.g. during local dev):
```bash
source .venv/bin/activate && export $(grep -v '^#' .env | xargs)
flask --app run.py db upgrade heads
```
✅ Expected: `Running upgrade <old> -> <new>...` per migration. Exit 0.

### Create a new migration
```bash
source .venv/bin/activate
flask --app run.py db migrate -m "describe what changed"
```
Then review the generated file in `migrations/versions/` — Alembic sometimes generates merge migrations with unused imports that fail ruff:
```bash
# Remove unused imports from the generated file before committing:
# Delete: "from alembic import op" and "import sqlalchemy as sa" if unused
ruff check migrations/
```
✅ Expected: `ruff check migrations/` passes with 0 errors.

### Check current migration state
```bash
flask --app run.py db current  # what revision is applied
flask --app run.py db history  # full chain
```

### Emergency downgrade (one step back)
```bash
source .venv/bin/activate && export $(grep -v '^#' .env | xargs)
flask --app run.py db downgrade -1
```
⚠️ Confirm data impact. Some downgrade() functions drop columns — data is lost.

---

## Table Health Check

Identify tables with bloat or missing indexes:
```bash
psql $DATABASE_URL << 'SQL'
SELECT
    schemaname,
    tablename,
    pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) AS total_size,
    n_dead_tup,
    n_live_tup,
    ROUND(n_dead_tup::numeric / NULLIF(n_live_tup + n_dead_tup, 0) * 100, 1) AS dead_pct,
    seq_scan,
    idx_scan
FROM pg_stat_user_tables
ORDER BY seq_scan DESC
LIMIT 15;
SQL
```
✅ Expected: `dead_pct` < 5% for all tables. `seq_scan` count low on large tables.

Tables with high `seq_scan` that lack indexes are the ones to address with a new migration.

---

## Sequential Scan Audit

Run monthly to catch new scan hotspots:
```bash
psql $DATABASE_URL << 'SQL'
SELECT
    relname AS table_name,
    seq_scan,
    seq_tup_read,
    idx_scan,
    n_live_tup AS row_est
FROM pg_stat_user_tables
WHERE seq_scan > 1000
ORDER BY seq_scan DESC;
SQL
```
Compare against the last audit. New entries warrant a migration adding an index.

---

## Backup

Neon provides point-in-time recovery (PITR) automatically for paid plans. For a manual snapshot:
```bash
pg_dump $DATABASE_URL \
    --format=custom \
    --compress=9 \
    --file="backup-edge-tracker-$(date +%Y%m%d-%H%M%S).dump"
```
✅ Expected: File created, size > 0. Spot-check:
```bash
pg_restore --list backup-edge-tracker-*.dump | grep -c "TABLE DATA"
```
✅ Expected: > 10 tables listed.

### Restore to local SQLite (for inspection)
```bash
# Not directly — use pg_restore to a local Postgres instead:
createdb edge_tracker_restore
pg_restore --dbname=edge_tracker_restore backup-edge-tracker-*.dump
psql edge_tracker_restore -c "\dt"
```

---

## Large Table Operations

Tables that may require care at scale:

| Table | Key concern | Mitigation |
|-------|------------|------------|
| `player_game_log` | Pruned by scheduler; check `cache_expires` | Scheduler prunes stale rows; check it's running |
| `bet_postmortem` | Grows with every settled bet; no TTL | Archive old records annually if table > 50k rows |
| `job_log` | Grows ~60 rows/day; no TTL | Purge rows older than 90 days (see below) |
| `odds_snapshots` | Multiple snapshots per game; no dedup | Monitor for unexpected growth |

### Prune old job_log rows (run quarterly)
```bash
psql $DATABASE_URL << 'SQL'
DELETE FROM job_log
WHERE started_at < NOW() - INTERVAL '90 days';
SQL
```
✅ Expected: `DELETE N` where N is reasonable (< 10k). If larger, investigate why so many runs accumulated.

---

## Staleness Check
| Config File | Affects Steps |
|-------------|---------------|
| `app/models.py` | All steps (source of truth for schema) |
| `migrations/versions/` | Migration steps |
| `docker-entrypoint.sh` | Deploy-time migration invocation |
