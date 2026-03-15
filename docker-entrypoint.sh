#!/usr/bin/env sh
set -e

# Run migrations once on container startup so required tables exist.
# Allow failure so the container can still serve /health during DB cold starts.
# Guard with timeout to avoid blocking web startup long enough to fail Railway healthchecks.
MIGRATION_MAX_SECONDS="${MIGRATION_MAX_SECONDS:-45}"
if command -v timeout >/dev/null 2>&1; then
  timeout "${MIGRATION_MAX_SECONDS}"s sh -c \
    'SCHEDULER_ENABLED=false flask --app run.py db upgrade heads' \
    || echo "WARNING: DB migration failed/timed out — continuing startup"
else
  SCHEDULER_ENABLED=false flask --app run.py db upgrade heads \
    || echo "WARNING: DB migration failed — continuing startup"
fi

exec gunicorn --config gunicorn.conf.py run:app
