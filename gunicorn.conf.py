"""Gunicorn configuration for production deployment."""
import os

# Network — Railway sets PORT dynamically; fall back to 8000 for local Docker
bind = f"0.0.0.0:{os.getenv('PORT', '8000')}"

# One worker is the safe baseline while the limiter uses memory://. Multiple
# workers require a shared RATELIMIT_STORAGE_URI and an explicit capacity review.
workers = int(os.getenv('WEB_CONCURRENCY', 1))
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
