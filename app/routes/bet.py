from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from app import db  # ✅ Corrected import for db
from app.models import Bet  # ✅ Import models correctly
from app.forms import BetForm  # ✅ Import forms correctly

# ✅ Define the Blueprint Correctly
bet = Blueprint('bet', __name__)

@bet.route('/bets', methods=['GET', 'POST'])
@login_required
def place_bet():
    form = BetForm()
    if form.validate_on_submit():
        new_bet = Bet(
            user_id=current_user.id,
            team_a=form.team_a.data,
            team_b=form.team_b.data,
            match_date=form.match_date.data,
            bet_amount=form.bet_amount.data,
            outcome=form.outcome.data
        )
        db.session.add(new_bet)
        db.session.commit()
        flash('Bet recorded successfully!', 'success')
        return redirect(url_for('bet.place_bet'))  

    bets = Bet.query.filter_by(user_id=current_user.id).all()
    return render_template('bets.html', form=form, bets=bets)

@bet.route('/view_bets')
@login_required
def view_bets():
    bets = Bet.query.filter_by(user_id=current_user.id).all()
    return render_template('view_bets.html', bets=bets)

@bet.route('/delete_bet/<int:bet_id>', methods=['POST'])
@login_required
def delete_bet(bet_id):
    bet = Bet.query.get_or_404(bet_id)

    # Ensure user can only delete their own bets
    if bet.user_id != current_user.id:
        flash("You don't have permission to delete this bet.", "danger")
        return redirect(url_for('bet.place_bet'))

    db.session.delete(bet)
    db.session.commit()
    flash('Bet deleted successfully!', 'success')
    return redirect(url_for('bet.place_bet'))