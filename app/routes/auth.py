import logging
import os
import threading
import time

from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import login_user, logout_user, current_user
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError

from app import db, limiter
from app.forms import LoginForm, LogoutForm, RegisterForm
from app.models import User

auth = Blueprint('auth', __name__)
logger = logging.getLogger(__name__)
SESSION_STARTED_AT_KEY = '_edge_session_started_at'


@auth.before_app_request
def enforce_absolute_session_lifetime():
    """Expire authenticated sessions even when their idle cookie keeps refreshing."""
    if not current_user.is_authenticated:
        return None

    now = int(time.time())
    started_at = session.get(SESSION_STARTED_AT_KEY)
    try:
        started_at = int(started_at)
    except (TypeError, ValueError):
        session[SESSION_STARTED_AT_KEY] = now
        return None

    max_age = int(current_app.config['SESSION_ABSOLUTE_LIFETIME'].total_seconds())
    if started_at <= now and now - started_at < max_age:
        return None

    logout_user()
    session.clear()
    if request.is_json or request.accept_mimetypes.best == 'application/json':
        return jsonify(error='session expired'), 401
    flash('Your session expired. Please sign in again.', 'info')
    return redirect(url_for('auth.login'))


def _maybe_trigger_auto_picks_on_login() -> None:
    """Optionally trigger auto-pick generation in the background after login."""
    if os.getenv('AUTO_PICKS_ON_LOGIN', 'false').lower() != 'true':
        return

    def _run():
        try:
            from app.services.scheduler import generate_daily_auto_picks
            generate_daily_auto_picks()
        except Exception as exc:
            logger.warning("Login-triggered auto-picks failed: %s", exc)

    threading.Thread(target=_run, daemon=True).start()


@auth.route('/register', methods=['GET', 'POST'])
@limiter.limit("5 per minute")
def register():
    if current_user.is_authenticated:
        flash('You are already logged in!', 'info')
        return redirect(url_for('main.home'))

    form = RegisterForm()
    if form.validate_on_submit():
        existing_user = User.query.filter(
            (User.username == form.username.data) | (User.email == form.email.data)
        ).first()

        if existing_user:
            flash('An account with that username or email already exists.', 'danger')
            return render_template('register.html', form=form)

        user = User(username=form.username.data, email=form.email.data)
        user.set_password(form.password.data)
        try:
            db.session.add(user)
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash('An account with that username or email already exists.', 'danger')
            return render_template('register.html', form=form)
        flash('Registration successful. Please log in.', 'success')
        return redirect(url_for('auth.login'))

    return render_template('register.html', form=form)


@auth.route('/login', methods=['GET', 'POST'])
@limiter.limit("10 per minute")
def login():
    if current_user.is_authenticated:
        flash('You are already logged in!', 'info')
        return redirect(url_for('main.home'))

    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()
        if user and user.check_password(form.password.data):
            login_user(user, remember=form.remember.data)
            session.permanent = True
            session[SESSION_STARTED_AT_KEY] = int(time.time())
            _maybe_trigger_auto_picks_on_login()
            flash('Login successful.', 'success')
            return redirect(url_for('main.home'))

        flash('Login failed. Check your username and password.', 'danger')

    return render_template('login.html', form=form)


@auth.route('/logout', methods=['POST'])
def logout():
    form = LogoutForm()
    if current_user.is_authenticated and request.method == 'POST' and not form.validate_on_submit():
        flash('Invalid logout request.', 'danger')
        return redirect(url_for('auth.login'))

    try:
        if current_user.is_authenticated:
            logout_user()
            session.pop(SESSION_STARTED_AT_KEY, None)
            flash('Logged out successfully.', 'success')
        else:
            flash('You are already logged out.', 'info')
    except (OperationalError, DBAPIError):
        db.session.rollback()
        flash('Session ended. Please log in again.', 'info')

    return redirect(url_for('auth.login'))
