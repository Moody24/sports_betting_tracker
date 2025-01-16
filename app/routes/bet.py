from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from app import db
from app.models import Bet
from app.forms import BetForm

bet = Blueprint('bet', __name__)

@bet.route('/bets', methods=['GET', 'POST'])
@login_required
def place_bet():
    form = BetForm()
    if form.validate_on_submit():
        bet = Bet(
            user_id=current_user.id,
            team_a=form.team_a.data,
            team_b=form.team_b.data,
            match_date=form.match_date.data,
            bet_amount=form.bet_amount.data,
            outcome=form.outcome.data
        )
        db.session.add(bet)
        db.session.commit()
        flash('Bet recorded successfully!', 'success')
        return redirect(url_for('bet.view_bets'))

    return render_template('bets.html', form=form)

@bet.route('/bets/view')
@login_required
def view_bets():
    bets = Bet.query.filter_by(user_id=current_user.id).all()
    return render_template('view_bets.html', bets=bets)
