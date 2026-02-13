from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager, current_user
import os
from datetime import datetime, timezone

from flask import Flask
from flask_login import LoginManager, current_user
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy

# ✅ Initialize extensions first
db = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()

def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'your-default-secret-key')
    app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'sqlite:///app.db')
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['WTF_CSRF_ENABLED'] = True
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

    # ✅ Initialize extensions AFTER app is created
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)

    login_manager.login_view = 'auth.login'
    login_manager.login_message_category = 'info'

    # ✅ Import models AFTER initializing db (avoiding circular import)
    from app.forms import LogoutForm
    from app.models import Bet, User

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

    # ✅ Import Blueprints AFTER initializing everything
    from app.routes.auth import auth
    from app.routes.bet import bet
    from app.routes.main import main

    app.register_blueprint(auth, url_prefix='/auth')
    app.register_blueprint(bet)  # ✅ No url_prefix to keep `/bets`
    app.register_blueprint(main)

    return app
