from flask import Blueprint, render_template, redirect, request, url_for, flash
from flask_login import login_user, logout_user, current_user
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError

from app import db, limiter
from app.forms import LoginForm, LogoutForm, RegisterForm
from app.models import User

auth = Blueprint('auth', __name__)


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
            flash('Login successful.', 'success')
            return redirect(url_for('main.home'))

        flash('Login failed. Check your username and password.', 'danger')

    return render_template('login.html', form=form)


@auth.route('/logout', methods=['GET', 'POST'])
def logout():
    form = LogoutForm()
    if current_user.is_authenticated and request.method == 'POST' and not form.validate_on_submit():
        flash('Invalid logout request.', 'danger')
        return redirect(url_for('auth.login'))

    try:
        if current_user.is_authenticated:
            logout_user()
            flash('Logged out successfully.', 'success')
        else:
            flash('You are already logged out.', 'info')
    except (OperationalError, DBAPIError):
        db.session.rollback()
        flash('Session ended. Please log in again.', 'info')

    return redirect(url_for('auth.login'))
