#!/usr/bin/env python3
"""Start the single-worker local app against the project PostgreSQL cluster."""

import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / '.env', override=False)

if not os.getenv('DATABASE_URL', '').startswith('postgresql:'):
    raise SystemExit('Set DATABASE_URL to the local PostgreSQL URL in .env first.')

subprocess.run([str(ROOT / 'scripts/local_postgres.sh'), 'start'], check=True)

environment = os.environ.copy()
arguments = sys.argv[1:]
if arguments not in ([], ['--with-scheduler']):
    raise SystemExit('Usage: .venv/bin/python scripts/run_local.py [--with-scheduler]')
environment['SCHEDULER_ENABLED'] = 'true' if arguments else 'false'

gunicorn = ROOT / '.venv/bin/gunicorn'
os.chdir(ROOT)
os.execve(str(gunicorn), [
    str(gunicorn), '--workers', '1', '--bind', '127.0.0.1:5000',
    '--timeout', '120', 'run:app',
], environment)
