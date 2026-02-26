import logging
import os
from datetime import datetime, timezone

from flask import Flask, render_template
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import LoginManager, current_user
from flask_migrate import Migrate, upgrade as _upgrade
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect

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
    default_limits=[],
    storage_uri=os.getenv('RATELIMIT_STORAGE_URI', 'memory://'),
)

def create_app(testing=False):
    app = Flask(__name__)
    secret_key = os.getenv('SECRET_KEY')
    if not secret_key:
        if testing:
            secret_key = 'test-only-insecure-key'
        else:
            raise RuntimeError(
                "SECRET_KEY environment variable is not set. "
                "Set it before starting the application."
            )
    app.config['SECRET_KEY'] = secret_key
    db_url = os.getenv('DATABASE_URL', 'sqlite:///app.db')
    # Railway Postgres URLs may start with postgres:// — SQLAlchemy 2.x requires postgresql://
    if db_url.startswith('postgres://'):
        db_url = db_url.replace('postgres://', 'postgresql://', 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = db_url
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['WTF_CSRF_ENABLED'] = True
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

    if testing:
        app.config['TESTING'] = True
        app.config['RATELIMIT_ENABLED'] = False

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)

    login_manager.login_view = 'auth.login'
    login_manager.login_message_category = 'info'

    from app.forms import LogoutForm
    from app.models import User

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    @app.context_processor
    def inject_user():
        return {
            'current_user': current_user,
            'current_year': datetime.now(timezone.utc).year,
            'logout_form': LogoutForm(),
        }

    @app.after_request
    def add_security_headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'SAMEORIGIN'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        return response

    # ── Liveness check (fast, always 200) ────────────────────────────
    @app.route('/health')
    def health():
        return {'status': 'healthy'}, 200

    from app.routes.auth import auth
    from app.routes.bet import bet
    from app.routes.main import main

    app.register_blueprint(auth, url_prefix='/auth')
    app.register_blueprint(bet)
    app.register_blueprint(main)

    @app.errorhandler(404)
    def not_found_error(e):
        return render_template('errors/404.html'), 404

    @app.errorhandler(500)
    def internal_error(e):
        db.session.rollback()
        return render_template('errors/500.html'), 500

    # ── Register CLI commands ───────────────────────────────────
    from app.cli import register_cli
    register_cli(app)

    # ── Auto-upgrade migrations (Docker entrypoint) ──────────────
    auto_upgrade = os.getenv('AUTO_DB_UPGRADE', 'false').lower() == 'true'

    if not app.config.get('TESTING') and auto_upgrade:
        with app.app_context():
            _upgrade()

    # ── Start background scheduler (production only) ─────────────
    if (
        not app.config.get('TESTING')
        and os.getenv('SCHEDULER_ENABLED', 'false').lower() == 'true'
    ):
        from app.services.scheduler import init_scheduler
        init_scheduler(app)

    return app
