"""Gunicorn configuration for production deployment."""
import os

# Network — Railway sets PORT dynamically; fall back to 8000 for local Docker
bind = f"0.0.0.0:{os.getenv('PORT', '8000')}"

# One worker is the safe baseline while the limiter uses memory://. Multiple
# workers require a shared RATELIMIT_STORAGE_URI and an explicit capacity review.
workers = int(os.getenv('WEB_CONCURRENCY', 1))
# Eight bounded SSE streams leave eight threads available for normal requests.
# The shared DB reader does not keep a connection checked out between polls.
worker_class = "gthread"
threads = 16

# Timeouts
timeout = 120
keepalive = 5

# Logging — send both to stdout so container runtimes capture them
accesslog = "-"
errorlog = "-"
loglevel = "info"

# Reload on code changes (development only — set via env in prod)
reload = False


def on_starting(server):
    """Validate effective settings after CLI and GUNICORN_CMD_ARGS overrides."""
    config = server.cfg
    if config.worker_class.__module__ != 'gunicorn.workers.gthread' or config.threads < 16:
        raise RuntimeError('Basketball SSE requires gthread with at least 16 threads.')
    production = bool(os.getenv('RAILWAY_ENVIRONMENT')) or os.getenv('FLASK_ENV') == 'production'
    if production and config.workers > 1 and os.getenv('RATELIMIT_STORAGE_URI', 'memory://').startswith('memory://'):
        raise RuntimeError('Multiple web workers require a shared limiter store.')
