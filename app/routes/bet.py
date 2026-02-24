from datetime import datetime, date as date_type

from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_required, current_user

from app import db
from app.forms import BetForm
from app.models import Bet
from app.services.nba_service import get_todays_games, resolve_pending_bets

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

    # Pre-populate from query params (used by NBA Today quick-add)
    if request.method == 'GET':
        if request.args.get('team_a'):
            form.team_a.data = request.args['team_a']
        if request.args.get('team_b'):
            form.team_b.data = request.args['team_b']
        if request.args.get('match_date'):
            try:
                form.match_date.data = datetime.strptime(request.args['match_date'], '%Y-%m-%d').date()
            except ValueError:
                pass
        if request.args.get('bet_type'):
            form.bet_type.data = request.args['bet_type']
        if request.args.get('over_under_line'):
            try:
                form.over_under_line.data = float(request.args['over_under_line'])
            except (ValueError, TypeError):
                pass
        if request.args.get('game_id'):
            form.external_game_id.data = request.args['game_id']

    if form.validate_on_submit():
        bet_obj = Bet(
            user_id=current_user.id,
            team_a=form.team_a.data,
            team_b=form.team_b.data,
            match_date=form.match_date.data,
            bet_amount=form.bet_amount.data,
            outcome=form.outcome.data,
            bet_type=form.bet_type.data,
            over_under_line=form.over_under_line.data if form.bet_type.data in ('over', 'under') else None,
            external_game_id=form.external_game_id.data or None,
        )
        db.session.add(bet_obj)
        db.session.commit()
        flash('Bet recorded successfully!', 'success')
        return redirect(url_for('bet.place_bet'))

    return render_template('bets/form.html', form=form, bet=None)


# ── NBA Today ────────────────────────────────────────────────────────


@bet.route('/nba/today')
@login_required
def nba_today():
    games = get_todays_games()

    # Gather user's pending O/U bets keyed by external_game_id
    pending = Bet.query.filter_by(
        user_id=current_user.id, outcome='pending'
    ).filter(Bet.external_game_id.isnot(None)).all()
    tracked = {b.external_game_id: b for b in pending}

    return render_template('bets/nba_today.html', games=games, tracked=tracked)


@bet.route('/nba/update-results', methods=['POST'])
@login_required
def nba_update_results():
    pending = Bet.query.filter_by(
        user_id=current_user.id, outcome='pending'
    ).filter(
        Bet.external_game_id.isnot(None),
        Bet.bet_type.in_(['over', 'under']),
    ).all()

    resolved = resolve_pending_bets(pending)
    count = 0
    for bet_obj, outcome, actual_total in resolved:
        bet_obj.outcome = outcome
        bet_obj.actual_total = actual_total
        count += 1

    if count:
        db.session.commit()
        flash(f'Updated {count} bet(s) with final results.', 'success')
    else:
        flash('No pending bets could be resolved yet.', 'info')

    return redirect(url_for('bet.nba_today'))


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
