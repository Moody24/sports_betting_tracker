from __future__ import annotations

import logging
import os
import secrets
import sys
from time import perf_counter
from datetime import datetime, timedelta, timezone

from flask import Flask, g, get_template_attribute, render_template, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import LoginManager, current_user
from flask_migrate import Migrate, upgrade as _upgrade
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from sqlalchemy.exc import DBAPIError, OperationalError

if 'unittest' in ' '.join(str(a).lower() for a in (sys.argv or [])):
    os.environ.setdefault('SECRET_KEY', 'test-only-insecure-key')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s: %(message)s',
)

db = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()
csrf = CSRFProtect()
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["200 per hour", "50 per minute"],
)


def _is_non_server_invocation(argv: list[str] | None = None) -> bool:
    """Detect one-off CLI/script invocations where scheduler should not start."""
    args = [str(a).lower() for a in (argv if argv is not None else (sys.argv or []))]
    argv0 = os.path.basename(args[0]) if args else ''
    joined = ' '.join(args)
    return (
        argv0 in {'flask', 'pytest', 'py.test', 'alembic', '-'}
        or 'unittest' in joined
        or 'pytest' in joined
        or (
            argv0 in {'python', 'python3'}
            and len(args) > 1
            and args[1] in {'-c', '-m', '-'}
        )
    )


# Endpoints a search engine may index. Everything else renders
# `noindex, nofollow`.
#
# This is an allowlist rather than a blocklist on purpose: it FAILS CLOSED, so
# a route added later is private until somebody deliberately publishes it. The
# opposite default leaks user-specific betting data — bet history, stakes,
# P/L — into a search index, and no later fix un-indexes it.
#
# Adding to this set is a publishing decision. `tests/test_crawler_register.py`
# asserts every HTML-rendering GET route resolves to exactly one register.
PUBLIC_ENDPOINTS = frozenset({
    'main.home',
})


def _database_url(testing: bool) -> str:
    if testing:
        return 'sqlite:///file:edge_tracker_testdb?mode=memory&cache=shared&uri=true'
    url = os.getenv('DATABASE_URL', 'sqlite:///app.db')
    return url.replace('postgres://', 'postgresql://', 1) if url.startswith(
        'postgres://'
    ) else url


def _positive_int_env(name: str, default: int) -> int:
    raw = os.getenv(name, '').strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logging.getLogger(__name__).warning(
            'Invalid %s=%r; using default %d', name, raw, default
        )
        return default
    if value <= 0:
        logging.getLogger(__name__).warning(
            '%s must be positive; using default %d', name, default
        )
        return default
    return value


def _database_engine_options(app: Flask, database_url: str) -> dict:
    options = dict(app.config.get('SQLALCHEMY_ENGINE_OPTIONS', {}))
    base_pool_options = {
        'pool_pre_ping': True,
        'pool_recycle': 300,
        'pool_timeout': 30,
    }
    if not database_url.startswith('sqlite'):
        base_pool_options['pool_size'] = int(os.getenv('DB_POOL_SIZE', '2'))
        base_pool_options['max_overflow'] = int(os.getenv('DB_MAX_OVERFLOW', '3'))
        options.update(base_pool_options)
    elif database_url.startswith('sqlite:///file:') and 'cache=shared' in database_url:
        options.update({'connect_args': {'check_same_thread': False}})
    else:
        options.update(base_pool_options)
    return options


def _configure_app(app: Flask, testing: bool) -> None:
    secret_key = os.getenv('SECRET_KEY')
    if not secret_key and testing:
        secret_key = 'test-only-insecure-key'
    if not secret_key:
        raise RuntimeError(
            'SECRET_KEY environment variable is not set. '
            'Set it before starting the application.'
        )
    database_url = _database_url(testing)
    session_idle_minutes = _positive_int_env('SESSION_IDLE_MINUTES', 30)
    session_absolute_hours = _positive_int_env('SESSION_ABSOLUTE_HOURS', 12)
    remember_cookie_days = _positive_int_env('REMEMBER_COOKIE_DAYS', 14)
    app.config.update(
        SECRET_KEY=secret_key,
        SQLALCHEMY_DATABASE_URI=database_url,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SQLALCHEMY_ENGINE_OPTIONS=_database_engine_options(app, database_url),
        MAX_CONTENT_LENGTH=10 * 1024 * 1024,
        WTF_CSRF_ENABLED=True,
        RATELIMIT_ENABLED=(
            os.getenv('RATELIMIT_ENABLED', 'true').lower() == 'true'
        ),
        RATELIMIT_STORAGE_URI=os.getenv('RATELIMIT_STORAGE_URI', 'memory://'),
        RATELIMIT_IN_MEMORY_FALLBACK_ENABLED=False,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax',
        SESSION_PROTECTION='strong',
        PERMANENT_SESSION_LIFETIME=timedelta(minutes=session_idle_minutes),
        SESSION_ABSOLUTE_LIFETIME=timedelta(hours=session_absolute_hours),
        SESSION_REFRESH_EACH_REQUEST=True,
        REMEMBER_COOKIE_HTTPONLY=True,
        REMEMBER_COOKIE_SAMESITE='Lax',
        REMEMBER_COOKIE_DURATION=timedelta(days=remember_cookie_days),
    )
    is_production = (
        bool(os.getenv('RAILWAY_ENVIRONMENT'))
        or os.getenv('FLASK_ENV') == 'production'
    )
    app.config['DEPLOYMENT_IS_PRODUCTION'] = is_production
    app.config['WEB_CONCURRENCY'] = _positive_int_env('WEB_CONCURRENCY', 1)
    secure_default = 'true' if is_production else 'false'
    app.config['SESSION_COOKIE_SECURE'] = (
        os.getenv('SESSION_COOKIE_SECURE', secure_default).lower() == 'true'
    )
    app.config['REMEMBER_COOKIE_SECURE'] = (
        os.getenv('REMEMBER_COOKIE_SECURE', secure_default).lower() == 'true'
    )
    if testing:
        app.config['TESTING'] = True
        app.config['RATELIMIT_ENABLED'] = False


def _initialize_extensions(app: Flask) -> None:
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)


def _validate_rate_limit_topology(app: Flask) -> None:
    """Fail closed when a hosted topology would weaken authentication limits."""
    if app.config.get('TESTING'):
        return
    web_concurrency = app.config.get('WEB_CONCURRENCY', 1)
    storage_uri = app.config.get('RATELIMIT_STORAGE_URI', 'memory://')
    rate_limiting_enabled = app.config.get('RATELIMIT_ENABLED', True)
    is_production = app.config.get('DEPLOYMENT_IS_PRODUCTION', False)

    if is_production and not rate_limiting_enabled:
        raise RuntimeError('RATELIMIT_ENABLED must be true in production.')
    if web_concurrency > 1 and storage_uri.startswith('memory://'):
        message = (
            "RATELIMIT_STORAGE_URI is 'memory://' with WEB_CONCURRENCY="
            f'{web_concurrency}; use one web worker or a shared limiter store.'
        )
        if is_production:
            raise RuntimeError(message)
        app.logger.warning(message)


def _register_login_loader() -> None:
    from app.models import User

    login_manager.login_view = 'auth.login'
    login_manager.login_message_category = 'info'

    @login_manager.user_loader
    def load_user(user_id):
        try:
            return db.session.get(User, int(user_id))
        except (OperationalError, DBAPIError):
            db.session.rollback()
            return None


def _csp_nonce() -> str:
    if not getattr(g, '_csp_nonce', None):
        g._csp_nonce = secrets.token_urlsafe(16)
    return g._csp_nonce


def _register_template_context(app: Flask) -> None:
    from app.config_display import get_template_display_config
    from app.forms import LogoutForm

    display_config = get_template_display_config()

    @app.template_global('icon')
    def icon(name, size=16, classes=''):
        return get_template_attribute('_macros.html', 'icon')(
            name, size, classes
        )

    @app.context_processor
    def inject_user():
        if request.endpoint in ('health', 'ready', 'healthcheck'):
            return {}
        return {
            'current_user': current_user,
            'current_year': datetime.now(timezone.utc).year,
            'logout_form': LogoutForm(),
            'csp_nonce': _csp_nonce(),
            'page_is_public': request.endpoint in PUBLIC_ENDPOINTS,
            **display_config,
        }


def _register_security_hooks(app: Flask) -> None:
    @app.after_request
    def add_security_headers(response):
        started = getattr(g, '_request_started_at', None)
        if started is not None:
            duration_ms = (perf_counter() - started) * 1000.0
            response.headers['X-Response-Time-ms'] = f'{duration_ms:.1f}'
            response.headers['Server-Timing'] = f'app;dur={duration_ms:.1f}'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'SAMEORIGIN'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; "
            f"script-src 'self' 'nonce-{_csp_nonce()}'; "
            "style-src 'self' 'unsafe-inline'; "
            "font-src 'self'; "
            "img-src 'self' data: https://a.espncdn.com; "
            "connect-src 'self'; "
            "frame-ancestors 'none';"
        )
        if not app.debug:
            response.headers['Strict-Transport-Security'] = (
                'max-age=31536000; includeSubDomains'
            )
        return response

    @app.before_request
    def mark_request_start():
        g._request_started_at = perf_counter()


def _register_http_routes(app: Flask) -> None:
    from app.routes.auth import auth
    from app.routes.bet import bet
    from app.routes.main import main

    @app.route('/health')
    def health():
        return {'status': 'healthy'}, 200

    @app.route('/favicon.ico')
    def favicon():
        favicon_path = os.path.join(app.static_folder, 'favicon.ico')
        return app.send_static_file('favicon.ico') if os.path.exists(
            favicon_path
        ) else ('', 204)

    app.register_blueprint(auth, url_prefix='/auth')
    app.register_blueprint(bet)
    app.register_blueprint(main)

    @app.errorhandler(404)
    def not_found_error(_error):
        return render_template('errors/404.html'), 404

    @app.errorhandler(500)
    def internal_error(_error):
        db.session.rollback()
        return render_template('errors/500.html'), 500


def _run_startup_tasks(app: Flask) -> None:
    auto_upgrade = os.getenv('AUTO_DB_UPGRADE', 'false').lower() == 'true'
    if not app.config.get('TESTING') and auto_upgrade:
        with app.app_context():
            _upgrade()
    running_cli = (
        os.getenv('FLASK_RUN_FROM_CLI', 'false').lower() == 'true'
        or os.getenv('RUNNING_CLI', '0') == '1'
        or _is_non_server_invocation()
    )
    scheduler_enabled = (
        os.getenv('SCHEDULER_ENABLED', 'false').lower() == 'true'
    )
    if not app.config.get('TESTING') and not running_cli and scheduler_enabled:
        from app.services.scheduler import init_scheduler
        init_scheduler(app)


def create_app(testing=False):
    app = Flask(__name__)
    _configure_app(app, testing)
    _validate_rate_limit_topology(app)
    _initialize_extensions(app)
    _register_login_loader()
    _register_template_context(app)
    _register_security_hooks(app)
    _register_http_routes(app)
    from app.cli import register_cli
    register_cli(app)
    _run_startup_tasks(app)
    return app
