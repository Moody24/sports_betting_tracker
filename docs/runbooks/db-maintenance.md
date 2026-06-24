# Database Maintenance Runbook — Edge Tracker
**Stack:** SQLite (local dev) · Flask-Migrate (Alembic) · SQLAlchemy
**Last verified:** 2026-06-24
**Source configs:** `migrations/`, `app/models.py`
**Schedule:** Migrations: on schema change · Manual review: monthly

> **Note:** This runbook covers local SQLite development. When reconnecting to PostgreSQL
> (e.g. Neon), update `DATABASE_URL` in `.env` — all migration commands remain identical.
> For PostgreSQL-specific maintenance (vacuums, pg_stat queries), see the archived Neon
> runbook at the bottom of this file.

---

## Connection

### SQLite (local dev)
```bash
# Open the database directly
sqlite3 instance/app.db

# Useful SQLite commands
.tables              # list all tables
.schema bet          # show table schema
.mode column         # readable column output
.headers on
```

### Verify the app can connect
```bash
source .venv/bin/activate
python3 -c "
from app import create_app, db
app = create_app()
with app.app_context():
    print(db.session.execute(db.text('SELECT count(*) FROM bet')).scalar(), 'bets')
"
```

---

## Migrations

### Apply pending migrations
```bash
source .venv/bin/activate
flask --app run.py db upgrade heads
```
✅ Expected: `Running upgrade <old> -> <new>...` per migration. Exit 0.

### Check current migration state
```bash
flask --app run.py db current   # what revision is applied
flask --app run.py db history   # full chain
```

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

### Emergency downgrade (one step back)
```bash
source .venv/bin/activate
flask --app run.py db downgrade -1
```
⚠️ Confirm data impact before running. Some `downgrade()` functions drop columns — data is lost.

---

## Table Health Check (SQLite)

Row counts across all tables:
```bash
sqlite3 instance/app.db << 'SQL'
SELECT name, (SELECT count(*) FROM sqlite_master WHERE type='table' AND name=m.name) as exists
FROM sqlite_master WHERE type='table' ORDER BY name;
SQL
```

Count rows per table:
```bash
sqlite3 instance/app.db "
SELECT 'bet' as tbl, count(*) FROM bet
UNION ALL SELECT 'player_game_log', count(*) FROM player_game_log
UNION ALL SELECT 'bet_postmortem', count(*) FROM bet_postmortem
UNION ALL SELECT 'odds_snapshots', count(*) FROM odds_snapshots
UNION ALL SELECT 'job_log', count(*) FROM job_log
UNION ALL SELECT 'model_metadata', count(*) FROM model_metadata;
"
```

---

## Backup (SQLite)

### Quick text dump
```bash
sqlite3 instance/app.db .dump > "backup-edge-tracker-$(date +%Y%m%d-%H%M%S).sql"
```
✅ Expected: File created, size > 0.

### Binary copy (faster for large DBs)
```bash
cp instance/app.db "instance/app.db.bak-$(date +%Y%m%d)"
```

### Restore from dump
```bash
sqlite3 instance/app_restored.db < backup-edge-tracker-YYYYMMDD-HHMMSS.sql
```

---

## Large Table Operations

Tables that may require care at scale:

| Table | Key concern | Mitigation |
|-------|------------|------------|
| `player_game_log` | Grows with every game pulled | Scheduler prunes stale rows; check it's running |
| `bet_postmortem` | Grows with every settled bet; no TTL | Archive old records annually if table > 50k rows |
| `job_log` | Grows ~60 rows/day; no TTL | Prune rows older than 90 days (see below) |
| `odds_snapshots` | Multiple snapshots per game | Monitor for unexpected growth |

### Prune old job_log rows (run quarterly)
```bash
sqlite3 instance/app.db "
DELETE FROM job_log
WHERE started_at < datetime('now', '-90 days');
"
```

---

## Data Quality

Run the built-in pollution report before any major operation:
```bash
source .venv/bin/activate
flask pollution-report           # audit only (read-only)
flask pollution-report --fix     # fix issues in place
```

---

## Switching to PostgreSQL

When reconnecting to a hosted PostgreSQL instance (e.g. Neon), update `.env`:
```env
DATABASE_URL=postgresql://user:password@host/dbname?sslmode=require
```

Then run migrations (same command as SQLite):
```bash
flask --app run.py db upgrade heads
```

Note: `pool_size` and `max_overflow` env vars only take effect for PostgreSQL connections — they are automatically skipped for SQLite.

---

## Staleness Check
| Config File | Affects Steps |
|-------------|---------------|
| `app/models.py` | All steps (source of truth for schema) |
| `migrations/versions/` | Migration steps |
| `docker-entrypoint.sh` | Deploy-time migration invocation (when deploying) |

---

## Archived: PostgreSQL / Neon Operations

> The following commands apply only when `DATABASE_URL` points to a PostgreSQL instance.

### Direct psql access
```bash
source .venv/bin/activate && export $(grep -v '^#' .env | xargs)
psql $DATABASE_URL
```

### Table health (PostgreSQL)
```sql
SELECT schemaname, tablename,
    pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) AS total_size,
    n_dead_tup, n_live_tup,
    ROUND(n_dead_tup::numeric / NULLIF(n_live_tup + n_dead_tup, 0) * 100, 1) AS dead_pct,
    seq_scan, idx_scan
FROM pg_stat_user_tables
ORDER BY seq_scan DESC LIMIT 15;
```

### PostgreSQL backup
```bash
pg_dump $DATABASE_URL --format=custom --compress=9 \
    --file="backup-edge-tracker-$(date +%Y%m%d-%H%M%S).dump"
```
