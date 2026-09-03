"""Bet CRUD routes: list, create, edit, grade, delete, export."""

import csv
import io
import logging
import time
from datetime import datetime, date as date_type

from flask import render_template, redirect, url_for, flash, request, jsonify, Response, abort
from flask_login import login_required, current_user
from sqlalchemy import case as sa_case
from sqlalchemy.orm import joinedload, selectinload

from app import db, limiter
from app.enums import BetType, Outcome
from app.forms import BetForm
from app.models import Bet, compute_bets_net_pl
from app.services.bet_context_service import create_pick_context_for_bet
from app.services.bet_placement_service import (
    BetPlacementError,
    normalize_single_bet_lines,
    parse_bonus_multiplier,
    parse_optional_units,
)
from app.services.nba_service import backfill_game_ids
from app.services.projection_engine import ProjectionEngine
from app.services.value_detector import ValueDetector
from app.services.postmortem_service import create_or_update_postmortem

logger = logging.getLogger(__name__)

# Backfill debounce: track which bet IDs we've already attempted to backfill
# this process session so we don't call ESPN + commit on every /bets GET.
_BACKFILL_ATTEMPTED: dict[int, float] = {}
_BACKFILL_TTL = 300  # 5 min retry window


def _escape_like(value: str) -> str:
    """Escape LIKE special characters so user input is treated as a literal string."""
    return value.replace('\\', '\\\\').replace('%', r'\%').replace('_', r'\_')


def _filtered_bets_query(user_id: int, args):
    """Build a filtered Bet query from request args.

    Shared by the bet list and CSV export endpoints to avoid duplication.
    """
    query = Bet.query.options(
        joinedload(Bet.pick_context),
        selectinload(Bet.postmortem),
    ).filter_by(user_id=user_id)

    status = args.get('status', '').strip()
    search_query = args.get('q', '').strip()
    start_date = args.get('start_date', '').strip()
    end_date = args.get('end_date', '').strip()
    bet_type_filter = args.get('type', '').strip()

    if status:
        query = query.filter(Bet.outcome == status)
    if search_query:
        safe_q = _escape_like(search_query)
        query = query.filter(
            Bet.team_a.ilike(f'%{safe_q}%', escape='\\') |
            Bet.team_b.ilike(f'%{safe_q}%', escape='\\') |
            Bet.player_name.ilike(f'%{safe_q}%', escape='\\')
        )
    if start_date:
        try:
            start_dt = datetime.strptime(start_date, '%Y-%m-%d')
            query = query.filter(Bet.match_date >= start_dt)
        except ValueError:
            pass
    if end_date:
        try:
            end_dt = datetime.strptime(end_date, '%Y-%m-%d')
            query = query.filter(Bet.match_date <= end_dt)
        except ValueError:
            pass
    if bet_type_filter == 'parlay':
        query = query.filter(Bet.is_parlay.is_(True))
    elif bet_type_filter == 'straight':
        query = query.filter(Bet.is_parlay.is_(False), Bet.player_name.is_(None))
    elif bet_type_filter == 'player_prop':
        query = query.filter(Bet.player_name.isnot(None))

    return query


# ── Routes ────────────────────────────────────────────────────────────────


def _prefill_new_bet_form(form, args) -> None:
    """Apply optional builder query parameters to the single-bet form."""
    if args.get('team_a'):
        form.team_a.data = args['team_a']
    if args.get('team_b'):
        form.team_b.data = args['team_b']
    if args.get('match_date'):
        try:
            form.match_date.data = datetime.strptime(
                args['match_date'], '%Y-%m-%d'
            ).date()
        except ValueError:
            pass
    if args.get('bet_type'):
        form.bet_type.data = args['bet_type']
    if args.get('over_under_line'):
        try:
            form.over_under_line.data = float(args['over_under_line'])
        except (TypeError, ValueError):
            pass
    if args.get('game_id'):
        form.external_game_id.data = args['game_id']


def _new_bet_error(form, current_tab: str, message: str):
    flash(message, 'danger')
    return render_template(
        'bets/form.html',
        form=form,
        bet=None,
        current_tab=current_tab,
    ), 400

@login_required
def place_bet():
    query = _filtered_bets_query(current_user.id, request.args)
    pending_first = sa_case((Bet.outcome == Outcome.PENDING.value, 0), else_=1)
    ordered_query = query.order_by(pending_first, Bet.match_date.desc())

    page = request.args.get('page', default=1, type=int) or 1
    per_page = 25
    pagination = ordered_query.paginate(page=page, per_page=per_page, error_out=False)
    bets = list(pagination.items)

    _now_bt = time.monotonic()
    pending_props = [
        b for b in bets
        if b.outcome == Outcome.PENDING.value
        and b.is_player_prop
        and not b.external_game_id
        and _now_bt - _BACKFILL_ATTEMPTED.get(b.id, 0) >= _BACKFILL_TTL
    ]
    if pending_props:
        for _b in pending_props:
            _BACKFILL_ATTEMPTED[_b.id] = _now_bt
        try:
            backfill_game_ids(pending_props)
        except Exception:
            logger.exception("Game-id backfill failed")

    status = request.args.get('status', '').strip()
    search_query = request.args.get('q', '').strip()
    start_date = request.args.get('start_date', '').strip()
    end_date = request.args.get('end_date', '').strip()
    bet_type_filter = request.args.get('type', '').strip()

    parlay_groups: dict = {}
    for b in bets:
        if b.is_parlay and b.parlay_id:
            parlay_groups.setdefault(b.parlay_id, []).append(b)

    all_filtered_bets = ordered_query.all()
    all_parlay_groups: dict = {}
    for b in all_filtered_bets:
        if b.is_parlay and b.parlay_id:
            all_parlay_groups.setdefault(b.parlay_id, []).append(b)

    parlay_status: dict = {}
    for pid, legs in all_parlay_groups.items():
        outcomes = [leg.outcome for leg in legs]
        leg_count = len(legs)
        for leg in legs:
            setattr(leg, "_parlay_legs_count", leg_count)
        if any(o == Outcome.LOSE.value for o in outcomes):
            parlay_status[pid] = 'lose'
        elif all(o == Outcome.WIN.value for o in outcomes):
            parlay_status[pid] = 'win'
        elif all(o in (Outcome.WIN.value, Outcome.PUSH.value) for o in outcomes):
            parlay_status[pid] = 'push'
        else:
            parlay_status[pid] = 'pending'

    parlay_pl_map: dict = {}
    parlay_game_count: dict = {}
    for pid, legs in all_parlay_groups.items():
        parlay_pl_map[pid] = Bet.parlay_profit_loss(legs)
        unique_matchups = {(leg.team_a, leg.team_b, leg.match_date.date()) for leg in legs}
        parlay_game_count[pid] = len(unique_matchups) or 1

    filters = {
        'status': status,
        'q': search_query,
        'start_date': start_date,
        'end_date': end_date,
        'type': bet_type_filter,
    }

    filter_stats = {
        'count': len(all_filtered_bets),
        'wins': sum(1 for b in all_filtered_bets if b.outcome == 'win'),
        'losses': sum(1 for b in all_filtered_bets if b.outcome == 'lose'),
        'pending': sum(1 for b in all_filtered_bets if b.outcome == 'pending'),
        'wagered': sum(b.bet_amount for b in all_filtered_bets),
        'net': compute_bets_net_pl(all_filtered_bets),
    }

    return render_template(
        'bets/list.html',
        bets=bets,
        filters=filters,
        parlay_status=parlay_status,
        parlay_pl_map=parlay_pl_map,
        parlay_game_count=parlay_game_count,
        filter_stats=filter_stats,
        now_date=date_type.today(),
        pagination=pagination,
    )


@login_required
def new_bet():
    form = BetForm()
    current_tab = request.values.get('current_tab', 'single').strip()
    if current_tab not in {'single', 'prop', 'parlay', 'screenshot'}:
        current_tab = 'single'

    if request.method == 'GET':
        _prefill_new_bet_form(form, request.args)

    if form.validate_on_submit():
        player_name = request.form.get('player_name') or None
        prop_type = request.form.get('prop_type') or None
        prop_line_val = request.form.get('prop_line', type=float)
        over_under_line_val = form.over_under_line.data
        if over_under_line_val is None and request.form.get('over_under_line'):
            over_under_line_val = request.form.get('over_under_line', type=float)

        picked_team = form.picked_team.data or None
        bonus_mult = parse_bonus_multiplier(request.form.get('bonus_multiplier'))
        american_odds_val = request.form.get('american_odds', type=int)
        units_val = parse_optional_units(request.form.get('units'))
        is_total_side = form.bet_type.data in (BetType.OVER.value, BetType.UNDER.value)
        try:
            normalized_prop_line, normalized_over_under_line = normalize_single_bet_lines(
                form.bet_type.data,
                prop_type,
                prop_line_val,
                over_under_line_val,
            )
        except BetPlacementError as exc:
            return _new_bet_error(form, current_tab, str(exc))

        bet_obj = Bet(
            user_id=current_user.id,
            team_a=form.team_a.data,
            team_b=form.team_b.data,
            match_date=form.match_date.data,
            bet_amount=form.bet_amount.data,
            units=units_val,
            outcome=form.outcome.data,
            american_odds=american_odds_val,
            bet_type=form.bet_type.data,
            over_under_line=normalized_over_under_line if is_total_side else None,
            external_game_id=form.external_game_id.data or None,
            player_name=player_name,
            prop_type=prop_type,
            prop_line=normalized_prop_line,
            picked_team=picked_team if form.bet_type.data == BetType.MONEYLINE.value else None,
            bonus_multiplier=bonus_mult,
            notes=form.notes.data or None,
        )
        db.session.add(bet_obj)
        db.session.flush()
        create_pick_context_for_bet(
            bet_obj=bet_obj,
            detector=ValueDetector(ProjectionEngine()),
            selected_odds=american_odds_val,
        )
        db.session.commit()
        flash('Bet recorded successfully!', 'success')
        return redirect(url_for('bet.place_bet'))

    remaining_bankroll = None
    if current_user.starting_bankroll:
        remaining_bankroll = round(current_user.starting_bankroll + current_user.net_profit_loss(), 2)
    return render_template('bets/form.html', form=form, bet=None, remaining_bankroll=remaining_bankroll, current_tab=current_tab)


@login_required
def edit_bet(bet_id):
    """Edit an existing bet post-placement."""
    found_bet = db.session.get(Bet, bet_id)
    if found_bet is None:
        abort(404)
    if found_bet.user_id != current_user.id:
        return jsonify({'error': 'Permission denied'}), 403

    data = request.get_json(silent=True) or {}
    if not data:
        return jsonify({'error': 'No data provided'}), 400

    EDITABLE_FIELDS = {
        'bet_amount', 'american_odds', 'notes',
        'outcome', 'over_under_line', 'prop_line', 'picked_team',
    }
    unknown = set(data.keys()) - EDITABLE_FIELDS
    if unknown:
        return jsonify({'error': f'Cannot edit field(s): {", ".join(sorted(unknown))}'}), 400

    changes = []

    if 'bet_amount' in data:
        try:
            val = float(data['bet_amount'])
            if val <= 0:
                return jsonify({'error': 'bet_amount must be positive'}), 400
        except (TypeError, ValueError):
            return jsonify({'error': 'bet_amount must be a number'}), 400
        found_bet.bet_amount = val
        changes.append('stake')

    if 'american_odds' in data:
        if data['american_odds'] is None:
            found_bet.american_odds = None
        else:
            try:
                val = int(data['american_odds'])
                if val == 0:
                    return jsonify({'error': 'american_odds cannot be 0'}), 400
            except (TypeError, ValueError):
                return jsonify({'error': 'american_odds must be an integer'}), 400
            found_bet.american_odds = val
        changes.append('odds')

    if 'notes' in data:
        found_bet.notes = str(data['notes'])[:2000] if data['notes'] is not None else None
        changes.append('notes')

    if 'outcome' in data:
        allowed_outcomes = {Outcome.WIN.value, Outcome.LOSE.value, Outcome.PENDING.value, Outcome.PUSH.value}
        if data['outcome'] not in allowed_outcomes:
            return jsonify({'error': f'outcome must be one of: {", ".join(sorted(allowed_outcomes))}'}), 400
        found_bet.outcome = data['outcome']
        changes.append('outcome')

    if 'over_under_line' in data:
        if data['over_under_line'] is None:
            found_bet.over_under_line = None
        else:
            try:
                found_bet.over_under_line = float(data['over_under_line'])
            except (TypeError, ValueError):
                return jsonify({'error': 'over_under_line must be a number'}), 400
        changes.append('line')

    if 'prop_line' in data:
        if data['prop_line'] is None:
            found_bet.prop_line = None
        else:
            try:
                found_bet.prop_line = float(data['prop_line'])
            except (TypeError, ValueError):
                return jsonify({'error': 'prop_line must be a number'}), 400
        changes.append('prop line')

    if 'picked_team' in data:
        found_bet.picked_team = str(data['picked_team'])[:80] if data['picked_team'] else None
        changes.append('picked team')

    if not changes:
        return jsonify({'success': True, 'message': 'No changes made'}), 200

    db.session.commit()
    return jsonify({'success': True, 'message': f'Updated: {", ".join(changes)}'}), 200


@login_required
def delete_bet(bet_id):
    found_bet = db.session.get(Bet, bet_id)
    if found_bet is None:
        abort(404)

    if found_bet.user_id != current_user.id:
        flash("You don't have permission to delete this bet.", 'danger')
        return redirect(url_for('bet.place_bet'))

    db.session.delete(found_bet)
    db.session.commit()
    flash('Bet deleted successfully!', 'success')
    return redirect(url_for('bet.place_bet'))


@login_required
def grade_bet(bet_id):
    """Manually set the outcome of a pending bet."""
    bet_obj = Bet.query.filter_by(id=bet_id, user_id=current_user.id).first_or_404()
    outcome = (request.form.get('outcome') or '').strip()
    if outcome not in ('win', 'lose', 'push'):
        flash('Invalid outcome.', 'danger')
        return redirect(request.referrer or url_for('bet.place_bet'))
    bet_obj.outcome = outcome
    try:
        sp = db.session.begin_nested()
        try:
            create_or_update_postmortem(bet_obj)
            sp.commit()
        except Exception:
            sp.rollback()
            logger.exception("Postmortem failed for manually graded bet_id=%s", bet_obj.id)
    finally:
        db.session.commit()
    flash(f'Bet graded as {outcome}.', 'success')
    return redirect(request.referrer or url_for('bet.place_bet'))


@login_required
@limiter.limit("10 per minute")
def export_bets():
    """Export all (optionally filtered) bets for the current user as a CSV file."""
    query = _filtered_bets_query(current_user.id, request.args)
    bets = query.order_by(Bet.match_date.desc()).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        'Date', 'Away Team', 'Home Team', 'Bet Type', 'Stake',
        'Odds', 'O/U Line', 'Player', 'Prop Type', 'Prop Line',
        'Picked Team', 'Outcome', 'P/L', 'Parlay', 'Bonus Mult', 'Notes',
    ])
    for b in bets:
        writer.writerow([
            b.match_date.strftime('%Y-%m-%d'),
            b.team_a,
            b.team_b,
            b.bet_type,
            f'{b.bet_amount:.2f}',
            b.american_odds or '',
            b.over_under_line or '',
            b.player_name or '',
            b.prop_type or '',
            b.prop_line or '',
            b.picked_team or '',
            b.outcome,
            f'{b.profit_loss():.2f}',
            'Yes' if b.is_parlay else 'No',
            f'{b.bonus_multiplier:.2f}',
            b.notes or '',
        ])

    output.seek(0)
    filename = f'bets_{current_user.username}_{date_type.today().isoformat()}.csv'
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )
