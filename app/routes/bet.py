import csv
import io
from datetime import datetime

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import db
from app.forms import BetForm, DeleteBetForm, FanDuelImportForm
from app.models import Bet

bet = Blueprint('bet', __name__)


def _safe_float(raw_value, fallback=0.0):
    if raw_value is None:
        return fallback
    cleaned = str(raw_value).replace('$', '').replace(',', '').strip()
    if not cleaned:
        return fallback
    try:
        return float(cleaned)
    except ValueError:
        return fallback


def _parse_american_odds(raw_value):
    if raw_value is None:
        return None
    cleaned = str(raw_value).replace('−', '-').replace('+', '').strip()
    if not cleaned:
        return None
    try:
        return int(cleaned)
    except ValueError:
        return None


def _parse_outcome(raw_result):
    value = (raw_result or '').strip().lower()
    if value in {'won', 'win', 'w'}:
        return 'win'
    if value in {'lost', 'lose', 'loss', 'l'}:
        return 'lose'
    return 'pending'


def _parse_match_date(raw_value):
    for fmt in ('%Y-%m-%d', '%m/%d/%Y', '%m/%d/%Y %H:%M', '%Y-%m-%d %H:%M:%S'):
        try:
            return datetime.strptime(str(raw_value).strip(), fmt)
        except (ValueError, TypeError):
            continue
    return datetime.utcnow()


def _split_matchup(raw_matchup):
    text = (raw_matchup or '').strip()
    if ' vs ' in text:
        return text.split(' vs ', 1)
    if ' @ ' in text:
        away, home = text.split(' @ ', 1)
        return home, away
    if ' v ' in text:
        return text.split(' v ', 1)
    return text[:80] or 'Unknown Team', 'Opponent'


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
    return render_template('bets/list.html', bets=bets, filters=filters, delete_form=DeleteBetForm())


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


@bet.route('/bets/import', methods=['GET', 'POST'])
@login_required
def import_fanduel_bets():
    form = FanDuelImportForm()
    if form.validate_on_submit():
        stream = io.StringIO(form.csv_file.data.stream.read().decode('utf-8-sig'))
        reader = csv.DictReader(stream)

        imported = 0
        for row in reader:
            matchup = row.get('Event') or row.get('Matchup') or row.get('event')
            team_a, team_b = _split_matchup(matchup)

            stake = _safe_float(row.get('Stake') or row.get('Wager') or row.get('Bet Amount'))
            if stake <= 0:
                continue

            outcome = _parse_outcome(row.get('Result') or row.get('Outcome') or row.get('Status'))
            odds = _parse_american_odds(row.get('Odds') or row.get('American Odds'))
            bet_type = (row.get('Bet Type') or row.get('Type') or '').lower()
            is_parlay = 'parlay' in bet_type
            match_date = _parse_match_date(row.get('Date') or row.get('Placed Date') or row.get('Match Date'))

            db.session.add(
                Bet(
                    user_id=current_user.id,
                    team_a=team_a[:80],
                    team_b=team_b[:80],
                    match_date=match_date,
                    bet_amount=stake,
                    outcome=outcome,
                    american_odds=odds,
                    is_parlay=is_parlay,
                    source='fanduel',
                )
            )
            imported += 1

        if imported:
            db.session.commit()
            flash(f'Imported {imported} FanDuel bet(s).', 'success')
            return redirect(url_for('bet.place_bet'))

        flash('No valid bet rows were found in the CSV.', 'warning')

    return render_template('bets/import.html', form=form)


@bet.route('/view_bets')
@login_required
def view_bets():
    return redirect(url_for('bet.place_bet'))


@bet.route('/delete_bet/<int:bet_id>', methods=['POST'])
@login_required
def delete_bet(bet_id):
    form = DeleteBetForm()
    if not form.validate_on_submit():
        flash('Invalid delete request.', 'danger')
        return redirect(url_for('bet.place_bet'))

    found_bet = Bet.query.get_or_404(bet_id)

    if found_bet.user_id != current_user.id:
        flash("You don't have permission to delete this bet.", 'danger')
        return redirect(url_for('bet.place_bet'))

    db.session.delete(found_bet)
    db.session.commit()
    flash('Bet deleted successfully!', 'success')
    return redirect(url_for('bet.place_bet'))
