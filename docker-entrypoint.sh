#!/usr/bin/env sh
set -e

# Run migrations once on container startup so required tables exist.
# Allow failure so the container can still serve /health during DB cold starts.
# Guard with timeout to avoid blocking web startup long enough to fail Railway healthchecks.
MIGRATION_MAX_SECONDS="${MIGRATION_MAX_SECONDS:-45}"
MIGRATE_CMD='import os; os.environ.setdefault("SCHEDULER_ENABLED","false");
from app import create_app
from flask_migrate import upgrade
app = create_app()
with app.app_context():
    upgrade(directory="migrations")'
if command -v timeout >/dev/null 2>&1; then
  timeout "${MIGRATION_MAX_SECONDS}"s python -c "${MIGRATE_CMD}" \
    || { RC=$?; [ $RC -eq 124 ] && echo "WARNING: Migration timed out after ${MIGRATION_MAX_SECONDS}s — proceeding" || exit $RC; }
else
  python -c "${MIGRATE_CMD}"
fi

exec gunicorn --config gunicorn.conf.py run:app
