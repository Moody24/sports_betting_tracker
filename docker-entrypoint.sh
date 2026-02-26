#!/usr/bin/env sh
set -e

# Run migrations once on container startup so required tables exist.
# Allow failure so the container can still serve /health during DB cold starts.
flask --app run.py db upgrade heads || echo "WARNING: DB migration failed — continuing startup"

exec gunicorn --config gunicorn.conf.py run:app
