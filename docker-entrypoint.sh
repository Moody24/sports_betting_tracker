#!/usr/bin/env sh
set -eu

exec gunicorn --config gunicorn.conf.py run:app
