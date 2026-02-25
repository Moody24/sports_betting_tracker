#!/usr/bin/env sh
set -e

# Run migrations once on container startup so required tables exist.
flask --app run.py db upgrade heads

exec gunicorn --config gunicorn.conf.py run:app
