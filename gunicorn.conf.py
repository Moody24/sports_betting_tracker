"""Gunicorn configuration for production deployment."""
import multiprocessing

# Network
bind = "0.0.0.0:8000"

# Workers: (2 x CPU cores) + 1 is the recommended starting point
workers = multiprocessing.cpu_count() * 2 + 1
worker_class = "sync"

# Timeouts
timeout = 120
keepalive = 5

# Logging — send both to stdout so container runtimes capture them
accesslog = "-"
errorlog = "-"
loglevel = "info"

# Reload on code changes (development only — set via env in prod)
reload = False
