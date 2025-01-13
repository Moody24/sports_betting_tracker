from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_user, logout_user, login_required, current_user
from app import db
from app.models import User, Bet, Match
from app.forms import LoginForm, RegisterForm, BetForm

main = Blueprint('main', __name__)

@main.route('/')
def home():
    return render_template('home.html')

@main.route('/bets', methods=['GET', 'POST'])
@login_required
def manage_bets():
    form = BetForm()
    if request.method == 'POST' and form.validate_on_submit():
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
    if request.method == 'POST' and form.validate_on_submit():
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
    form = LoginForm()
    if request.method == 'POST' and form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()
        if user and user.password_hash == form.password.data:  # Simplified for now
            login_user(user)
            flash('Logged in successfully!', 'success')
            return redirect(url_for('main.home'))
        flash('Invalid username or password.', 'danger')
    return render_template('login.html', form=form)

@main.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Logged out successfully.', 'success')
    return redirect(url_for('main.home'))

