from datetime import datetime

from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user

from app import db
from app.forms import BetForm
from app.models import Bet

bet = Blueprint('bet', __name__)


@bet.route('/bets', methods=['GET'])
@login_required
def place_bet():
    query = Bet.query.filter_by(user_id=current_user.id)

    status = request.args.get('status', '').strip()
    search_query = request.args.get('q', '').strip()
    start_date = request.args.get('start_date', '').strip()
    end_date = request.args.get('end_date', '').strip()

    if status:
        query = query.filter(Bet.outcome == status)
    if search_query:
        query = query.filter((Bet.team_a.ilike(f'%{search_query}%')) | (Bet.team_b.ilike(f'%{search_query}%')))
    if start_date:
        try:
            start_dt = datetime.strptime(start_date, '%Y-%m-%d')
            query = query.filter(Bet.match_date >= start_dt)
        except ValueError:
            start_date = ''
    if end_date:
        try:
            end_dt = datetime.strptime(end_date, '%Y-%m-%d')
            query = query.filter(Bet.match_date <= end_dt)
        except ValueError:
            end_date = ''

    bets = query.order_by(Bet.match_date.desc()).all()

    filters = {
        'status': status,
        'q': search_query,
        'start_date': start_date,
        'end_date': end_date,
    }
    return render_template('bets/list.html', bets=bets, filters=filters)


@bet.route('/bets/new', methods=['GET', 'POST'])
@login_required
def new_bet():
    form = BetForm()
    if form.validate_on_submit():
        new_bet = Bet(
            user_id=current_user.id,
            team_a=form.team_a.data,
            team_b=form.team_b.data,
            match_date=form.match_date.data,
            bet_amount=form.bet_amount.data,
            outcome=form.outcome.data,
        )
        db.session.add(new_bet)
        db.session.commit()
        flash('Bet recorded successfully!', 'success')
        return redirect(url_for('bet.place_bet'))

    return render_template('bets/form.html', form=form, bet=None)


@bet.route('/view_bets')
@login_required
def view_bets():
    return redirect(url_for('bet.place_bet'))


@bet.route('/delete_bet/<int:bet_id>', methods=['POST'])
@login_required
def delete_bet(bet_id):
    found_bet = Bet.query.get_or_404(bet_id)

    if found_bet.user_id != current_user.id:
        flash("You don't have permission to delete this bet.", 'danger')
        return redirect(url_for('bet.place_bet'))

    db.session.delete(found_bet)
    db.session.commit()
    flash('Bet deleted successfully!', 'success')
    return redirect(url_for('bet.place_bet'))
