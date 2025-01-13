from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_user, logout_user, login_required, current_user
from app import db, bcrypt
from app.models import User, Bet, Match
from app.forms import LoginForm, RegisterForm, BetForm

main = Blueprint('main', __name__)

@main.route('/')
def home():
    return render_template('home.html')

@main.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        flash('You are already logged in!', 'info')
        return redirect(url_for('main.home'))

    form = RegisterForm()
    if form.validate_on_submit():
        if User.query.filter_by(email=form.email.data).first():
            flash('Email is already registered. Please log in.', 'danger')
            return redirect(url_for('main.login'))
        if User.query.filter_by(username=form.username.data).first():
            flash('Username is already taken. Please choose another.', 'danger')
            return redirect(url_for('main.register'))

        hashed_password = bcrypt.generate_password_hash(form.password.data).decode('utf-8')
        new_user = User(
            username=form.username.data,
            email=form.email.data,
            password_hash=hashed_password
        )
        db.session.add(new_user)
        db.session.commit()
        flash('Account created successfully! You can now log in.', 'success')
        return redirect(url_for('main.login'))
    
    return render_template('register.html', form=form)

@main.route('/bets', methods=['GET', 'POST'])
@login_required
def manage_bets():
    form = BetForm()
    if form.validate_on_submit():
        bet = Bet(
            user_id=current_user.id,
            match_id=form.match_id.data,
            bet_amount=form.bet_amount.data,
            outcome=form.outcome.data,
        )
        db.session.add(bet)
        db.session.commit()
        flash('Bet placed successfully!', 'success')
        return redirect(url_for('main.manage_bets'))
    bets = Bet.query.filter_by(user_id=current_user.id).all()
    return render_template('bets.html', form=form, bets=bets)

@main.route('/bets/edit/<int:bet_id>', methods=['GET', 'POST'])
@login_required
def edit_bet(bet_id):
    bet = Bet.query.get_or_404(bet_id)
    if bet.user_id != current_user.id:
        flash('You do not have permission to edit this bet.', 'danger')
        return redirect(url_for('main.manage_bets'))
    form = BetForm(obj=bet)
    if form.validate_on_submit():
        bet.match_id = form.match_id.data
        bet.bet_amount = form.bet_amount.data
        bet.outcome = form.outcome.data
        db.session.commit()
        flash('Bet updated successfully!', 'success')
        return redirect(url_for('main.manage_bets'))
    return render_template('edit_bet.html', form=form, bet=bet)

@main.route('/bets/delete/<int:bet_id>', methods=['POST'])
@login_required
def delete_bet(bet_id):
    bet = Bet.query.get_or_404(bet_id)
    if bet.user_id != current_user.id:
        flash('You do not have permission to delete this bet.', 'danger')
        return redirect(url_for('main.manage_bets'))
    db.session.delete(bet)
    db.session.commit()
    flash('Bet deleted successfully.', 'success')
    return redirect(url_for('main.manage_bets'))

@main.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        flash('You are already logged in!', 'info')
        return redirect(url_for('main.home'))
    
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()
        if user and bcrypt.check_password_hash(user.password_hash, form.password.data):
            login_user(user, remember=form.remember.data)
            flash('Logged in successfully!', 'success')
            next_page = request.args.get('next')
            return redirect(next_page) if next_page else redirect(url_for('main.home'))
        flash('Invalid username or password.', 'danger')
    return render_template('login.html', form=form)

@main.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Logged out successfully.', 'success')
    return redirect(url_for('main.home'))
