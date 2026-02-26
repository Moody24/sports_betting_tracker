"""Gunicorn configuration for production deployment."""
import os

# Network — Railway sets PORT dynamically; fall back to 8000 for local Docker
bind = f"0.0.0.0:{os.getenv('PORT', '8000')}"

# Workers: keep low on Railway's hobby tier (512MB RAM)
# Each sync worker uses ~50-80MB; 2 workers is safe and leaves headroom
workers = int(os.getenv('WEB_CONCURRENCY', 2))
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
