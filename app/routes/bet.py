import csv
import io
import json
import logging
import re
import time
from difflib import SequenceMatcher
from datetime import datetime, date as date_type, timezone, timedelta

from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify, Response
from flask_login import login_required, current_user
import requests

from app import db
from app.enums import BetSource, BetType, Outcome
from app.forms import BetForm
from app.models import Bet, GameSnapshot
from app.services.nba_service import (
    get_todays_games,
    fetch_upcoming_games,
    fetch_player_props_for_event,
    resolve_pending_bets,
    get_player_props,
    fetch_espn_boxscore,
    ESPN_SUMMARY_URL,
)
from app.services.projection_engine import ProjectionEngine
from app.services.value_detector import ValueDetector, quarter_kelly
from app.services.stats_service import find_player_id, get_cached_logs, get_player_stats_summary

logger = logging.getLogger(__name__)

bet = Blueprint('bet', __name__)
_PROP_PROGRESS_CACHE: dict[tuple, dict] = {}
_PROP_PROGRESS_TTL_SECONDS = 30


def _escape_like(value: str) -> str:
    """Escape LIKE special characters so user input is treated as a literal string."""
    return value.replace('\\', '\\\\').replace('%', r'\%').replace('_', r'\_')


def _normalize_name(value: str) -> str:
    return re.sub(r'[^a-z0-9]+', ' ', (value or '').lower()).strip()


def _filtered_bets_query(user_id: int, args) -> "db.Query":
    """Build a filtered Bet query from request args.

    Shared by the bet list and CSV export endpoints to avoid duplication.
    """
    query = Bet.query.filter_by(user_id=user_id)

    status = args.get('status', '').strip()
    search_query = args.get('q', '').strip()
    start_date = args.get('start_date', '').strip()
    end_date = args.get('end_date', '').strip()

    if status:
        query = query.filter(Bet.outcome == status)
    if search_query:
        safe_q = _escape_like(search_query)
        query = query.filter(
            Bet.team_a.ilike(f'%{safe_q}%', escape='\\') |
            Bet.team_b.ilike(f'%{safe_q}%', escape='\\')
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

    return query


@bet.route('/bets', methods=['GET'])
@login_required
def place_bet():
    query = _filtered_bets_query(current_user.id, request.args)
    bets = query.order_by(Bet.match_date.desc()).all()

    status = request.args.get('status', '').strip()
    search_query = request.args.get('q', '').strip()
    start_date = request.args.get('start_date', '').strip()
    end_date = request.args.get('end_date', '').strip()

    # Group parlay legs so the template can render them together
    parlay_groups: dict = {}
    for b in bets:
        if b.is_parlay and b.parlay_id:
            parlay_groups.setdefault(b.parlay_id, []).append(b)

    # Compute per-parlay overall outcome for display
    parlay_status: dict = {}
    for pid, legs in parlay_groups.items():
        outcomes = [l.outcome for l in legs]
        if any(o == Outcome.LOSE.value for o in outcomes):
            parlay_status[pid] = 'lose'
        elif all(o == Outcome.WIN.value for o in outcomes):
            parlay_status[pid] = 'win'
        else:
            parlay_status[pid] = 'pending'

    filters = {
        'status': status,
        'q': search_query,
        'start_date': start_date,
        'end_date': end_date,
    }

    # Summary stats for the current filtered view
    graded = [b for b in bets if b.outcome in ('win', 'lose')]
    filter_stats = {
        'count': len(bets),
        'wins': sum(1 for b in bets if b.outcome == 'win'),
        'losses': sum(1 for b in bets if b.outcome == 'lose'),
        'pending': sum(1 for b in bets if b.outcome == 'pending'),
        'wagered': sum(b.bet_amount for b in bets),
        'net': sum(b.profit_loss() for b in bets),
    }

    return render_template(
        'bets/list.html',
        bets=bets,
        filters=filters,
        parlay_status=parlay_status,
        filter_stats=filter_stats,
    )


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
        player_name = request.form.get('player_name') or None
        prop_type = request.form.get('prop_type') or None
        prop_line_val = None
        if request.form.get('prop_line'):
            try:
                prop_line_val = float(request.form['prop_line'])
            except (ValueError, TypeError):
                pass

        over_under_line_val = form.over_under_line.data
        if over_under_line_val is None and request.form.get('over_under_line'):
            try:
                over_under_line_val = float(request.form['over_under_line'])
            except (ValueError, TypeError):
                pass

        picked_team = form.picked_team.data or None

        bonus_mult = 1.0
        try:
            bm = float(request.form.get('bonus_multiplier', '1.0') or '1.0')
            if bm >= 1.0:
                bonus_mult = bm
        except (ValueError, TypeError):
            pass

        units_val = None
        if request.form.get('units'):
            try:
                parsed_units = float(request.form.get('units'))
                if parsed_units > 0:
                    units_val = parsed_units
            except (ValueError, TypeError):
                units_val = None

        normalized_prop_line = prop_line_val
        normalized_over_under_line = over_under_line_val

        is_total_side = form.bet_type.data in (BetType.OVER.value, BetType.UNDER.value)
        if is_total_side and not prop_type:
            if normalized_over_under_line is None and normalized_prop_line is not None:
                normalized_over_under_line = normalized_prop_line
                normalized_prop_line = None
            else:
                normalized_prop_line = None

            if normalized_over_under_line is None:
                flash('A line is required for totals (Over/Under).', 'danger')
                return render_template('bets/form.html', form=form, bet=None), 400

        if is_total_side and prop_type:
            normalized_prop_line = prop_line_val
            normalized_over_under_line = None
            if normalized_prop_line is None:
                flash('A prop line is required for player props.', 'danger')
                return render_template('bets/form.html', form=form, bet=None), 400

        bet_obj = Bet(
            user_id=current_user.id,
            team_a=form.team_a.data,
            team_b=form.team_b.data,
            match_date=form.match_date.data,
            bet_amount=form.bet_amount.data,
            units=units_val,
            outcome=form.outcome.data,
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
        db.session.commit()
        flash('Bet recorded successfully!', 'success')
        return redirect(url_for('bet.place_bet'))

    return render_template('bets/form.html', form=form, bet=None)


# ── NBA Today ────────────────────────────────────────────────────────


@bet.route('/nba/today')
@login_required
def nba_today():
    games = get_todays_games()
    upcoming_games = fetch_upcoming_games()
    today = date_type.today()

    # ── Upsert snapshots for today's games ──────────────────────────
    for game in games:
        snap = GameSnapshot.query.filter_by(
            espn_id=game['espn_id'], game_date=today
        ).first()

        if snap is None:
            # First view: lock in odds/moneyline now
            snap = GameSnapshot(
                espn_id=game['espn_id'],
                game_date=today,
                home_team=game['home']['name'],
                away_team=game['away']['name'],
                home_logo=game['home'].get('logo', ''),
                away_logo=game['away'].get('logo', ''),
                home_score=game['home']['score'],
                away_score=game['away']['score'],
                status=game['status'],
                over_under_line=game.get('over_under_line'),
                moneyline_home=game.get('moneyline_home'),
                moneyline_away=game.get('moneyline_away'),
                is_final=(game['status'] == 'STATUS_FINAL'),
            )
            db.session.add(snap)
        else:
            # Subsequent view: update live data but never overwrite locked odds
            snap.home_score = game['home']['score']
            snap.away_score = game['away']['score']
            snap.status = game['status']
            if game['status'] == 'STATUS_FINAL':
                snap.is_final = True
            # Backfill logos/moneyline if they were missing before
            if not snap.home_logo:
                snap.home_logo = game['home'].get('logo', '')
            if not snap.away_logo:
                snap.away_logo = game['away'].get('logo', '')

    db.session.commit()

    # Separate active (non-final) vs completed today
    active_games = [g for g in games if g['status'] != 'STATUS_FINAL']
    completed_snaps = (
        GameSnapshot.query
        .filter_by(game_date=today, is_final=True)
        .order_by(GameSnapshot.snapshot_time)
        .all()
    )

    # Gather user's pending bets keyed by external_game_id
    pending = Bet.query.filter_by(
        user_id=current_user.id, outcome=Outcome.PENDING.value
    ).filter(Bet.external_game_id.isnot(None)).all()
    tracked = {b.external_game_id: b for b in pending}

    return render_template(
        'bets/nba_today.html',
        games=active_games,
        completed_snaps=completed_snaps,
        upcoming_games=upcoming_games,
        tracked=tracked,
    )


@bet.route('/nba/update-results', methods=['POST'])
@login_required
def nba_update_results():
    # Resolve ALL pending bets that have an external_game_id
    pending = Bet.query.filter_by(
        user_id=current_user.id, outcome=Outcome.PENDING.value
    ).filter(Bet.external_game_id.isnot(None)).all()

    resolved = resolve_pending_bets(pending)
    count = 0
    for bet_obj, outcome, actual_value in resolved:
        bet_obj.outcome = outcome
        bet_obj.actual_total = actual_value
        count += 1

    if count:
        db.session.commit()
        flash(f'Updated {count} bet(s) with final results.', 'success')
    else:
        flash('No pending bets could be resolved yet.', 'info')

    return redirect(request.referrer or url_for('bet.place_bet'))


# ── Upcoming Games API (for bet builder game picker) ─────────────────


@bet.route('/nba/upcoming-games')
@login_required
def nba_upcoming_games():
    """Return today's + tomorrow's games as JSON for the bet builder picker."""
    today_games = get_todays_games()
    tomorrow_games = fetch_upcoming_games()

    results = []
    for g in today_games:
        results.append({
            'label': f"{g['away']['name']} @ {g['home']['name']}",
            'team_a': g['away']['name'],
            'team_b': g['home']['name'],
            'match_date': g['start_time'][:10] if g.get('start_time') else '',
            'game_id': g['espn_id'],
            'over_under_line': g.get('over_under_line'),
        })
    for g in tomorrow_games:
        results.append({
            'label': f"{g['away']['name']} @ {g['home']['name']} (Tomorrow)",
            'team_a': g['away']['name'],
            'team_b': g['home']['name'],
            'match_date': g.get('match_date', ''),
            'game_id': g['espn_id'],
            'over_under_line': g.get('over_under_line'),
        })

    return jsonify(results)


# ── Player Props API ──────────────────────────────────────────────


@bet.route('/nba/props/<espn_id>')
@login_required
def nba_props(espn_id):
    """Return player props for a game as JSON and persist them to snapshot."""
    props = get_player_props(espn_id)

    # Save props to today's snapshot if not already stored
    today = date_type.today()
    snap = GameSnapshot.query.filter_by(espn_id=espn_id, game_date=today).first()
    if snap and snap.props_json is None and props:
        snap.props_json = json.dumps(props)
        db.session.commit()

    return jsonify(props)


@bet.route('/nba/prop-progress/<espn_id>')
@login_required
def nba_prop_progress(espn_id):
    player_name = (request.args.get('player') or '').strip()
    prop_type = (request.args.get('prop_type') or '').strip()
    if not player_name or not prop_type:
        return jsonify({'ok': False, 'error': 'player and prop_type are required'}), 400

    cache_key = (espn_id, _normalize_name(player_name), prop_type)
    cached = _PROP_PROGRESS_CACHE.get(cache_key)
    now_monotonic = time.monotonic()
    if cached and cached.get('expires_at', 0) > now_monotonic:
        return jsonify(cached['payload'])

    boxscore = fetch_espn_boxscore(espn_id)
    if not boxscore:
        payload = {'ok': False, 'error': 'No boxscore data available yet'}
        _PROP_PROGRESS_CACHE[cache_key] = {
            'expires_at': now_monotonic + _PROP_PROGRESS_TTL_SECONDS,
            'payload': payload,
        }
        return jsonify(payload), 404

    target = _normalize_name(player_name)
    best_name = None
    best_stats = None
    best_score = 0.0
    for candidate_name, stats in boxscore.items():
        candidate_norm = _normalize_name(candidate_name)
        if not candidate_norm:
            continue
        score = SequenceMatcher(None, target, candidate_norm).ratio()
        if target == candidate_norm:
            score = 1.0
        elif target in candidate_norm or candidate_norm in target:
            score = max(score, 0.92)
        if score > best_score:
            best_score = score
            best_name = candidate_name
            best_stats = stats

    if best_name is None or best_score < 0.72:
        payload = {'ok': False, 'error': f'Player not found in boxscore for {player_name}'}
        _PROP_PROGRESS_CACHE[cache_key] = {
            'expires_at': now_monotonic + _PROP_PROGRESS_TTL_SECONDS,
            'payload': payload,
        }
        return jsonify(payload), 404

    stat_val = None if not best_stats else best_stats.get(prop_type)
    if stat_val is None:
        payload = {
            'ok': False,
            'error': f'Stat {prop_type} unavailable for {best_name}',
            'player': best_name,
        }
        _PROP_PROGRESS_CACHE[cache_key] = {
            'expires_at': now_monotonic + _PROP_PROGRESS_TTL_SECONDS,
            'payload': payload,
        }
        return jsonify(payload), 404

    status_text = 'Status unavailable'
    try:
        resp = requests.get(ESPN_SUMMARY_URL, params={'event': espn_id}, timeout=8)
        resp.raise_for_status()
        summary_data = resp.json()
        status_type = (
            summary_data.get('header', {})
            .get('competitions', [{}])[0]
            .get('status', {})
            .get('type', {})
        )
        detail = status_type.get('detail') or status_type.get('description') or ''
        status_name = status_type.get('name') or 'UNKNOWN'
        status_text = f'{status_name}: {detail}'.strip(': ').strip()
    except Exception:
        pass

    payload = {
        'ok': True,
        'player': best_name,
        'prop_type': prop_type,
        'stat': stat_val,
        'status': status_text,
        'match_score': round(best_score, 3),
    }
    _PROP_PROGRESS_CACHE[cache_key] = {
        'expires_at': now_monotonic + _PROP_PROGRESS_TTL_SECONDS,
        'payload': payload,
    }
    return jsonify(payload)


@bet.route('/nba/place-bets', methods=['POST'])
@login_required
def nba_place_bets():
    """Place one or more prop bets from the bet slip.

    Expects JSON body:
    {
        "stake": 25.0,
        "is_parlay": false,
        "legs": [
            {
                "player_name": "LeBron James",
                "prop_type": "player_points",
                "prop_line": 25.5,
                "bet_type": "over",
                "american_odds": -115,
                "team_a": "...",
                "team_b": "...",
                "game_id": "...",
                "match_date": "2026-02-24"
            }
        ]
    }
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Invalid request"}), 400

    legs = data.get("legs", [])
    is_parlay = bool(data.get("is_parlay", False))

    if not legs:
        return jsonify({"error": "No selections provided"}), 400

    try:
        stake = float(data.get("stake") or 0)
    except (TypeError, ValueError):
        return jsonify({"error": "Stake must be a number"}), 400
    if stake <= 0:
        return jsonify({"error": "Stake must be greater than zero"}), 400

    units_payload = data.get("units")
    units_val = None
    if units_payload is not None:
        try:
            parsed_units = float(units_payload)
            if parsed_units > 0:
                units_val = parsed_units
        except (TypeError, ValueError):
            units_val = None

    try:
        bonus_mult = float(data.get("bonus_multiplier") or 1.0)
        if bonus_mult < 1.0:
            bonus_mult = 1.0
    except (TypeError, ValueError):
        bonus_mult = 1.0

    parlay_id = Bet.generate_parlay_id() if is_parlay else None

    created = []
    errors = []
    for i, leg in enumerate(legs):
        if not isinstance(leg, dict):
            errors.append(f"Leg {i + 1}: must be an object")
            continue
        if not leg.get("team_a") or not leg.get("team_b"):
            errors.append(f"Leg {i + 1}: team_a and team_b are required")
            continue

        try:
            match_date = datetime.strptime(leg.get("match_date", ""), "%Y-%m-%d")
        except ValueError:
            match_date = datetime.now(timezone.utc)

        try:
            prop_line_val = float(leg["prop_line"]) if leg.get("prop_line") is not None else None
        except (TypeError, ValueError):
            prop_line_val = None

        try:
            american_odds_val = int(leg["american_odds"]) if leg.get("american_odds") is not None else None
        except (TypeError, ValueError):
            american_odds_val = None

        player_name_val = str(leg.get("player_name") or "")[:100] or None
        prop_type_val = str(leg.get("prop_type") or "")[:40] or None
        is_player_prop = bool(player_name_val and prop_type_val and prop_line_val is not None)

        over_under_line_val = None
        if not is_player_prop and leg.get("over_under_line") is not None:
            try:
                over_under_line_val = float(leg.get("over_under_line"))
            except (TypeError, ValueError):
                over_under_line_val = None
        if not is_player_prop and over_under_line_val is None and prop_line_val is not None:
            # Backward-compatible fallback for totals if clients still send prop_line.
            over_under_line_val = prop_line_val

        bet_obj = Bet(
            user_id=current_user.id,
            team_a=str(leg["team_a"])[:80],
            team_b=str(leg["team_b"])[:80],
            match_date=match_date,
            bet_amount=stake,
            units=units_val,
            outcome=Outcome.PENDING.value,
            bet_type=leg.get("bet_type", BetType.OVER.value),
            over_under_line=None if is_player_prop else over_under_line_val,
            american_odds=american_odds_val,
            external_game_id=leg.get("game_id") or None,
            player_name=player_name_val,
            prop_type=prop_type_val,
            prop_line=prop_line_val if is_player_prop else None,
            is_parlay=is_parlay,
            parlay_id=parlay_id,
            source=BetSource.NBA_PROPS.value,
            bonus_multiplier=bonus_mult,
        )
        db.session.add(bet_obj)
        created.append(bet_obj)

    if errors:
        db.session.rollback()
        return jsonify({"error": "; ".join(errors)}), 400

    db.session.commit()

    if is_parlay:
        msg = f"Parlay with {len(created)} leg(s) placed — ${stake:.2f} wagered!"
    else:
        msg = f"{len(created)} bet(s) placed — ${stake * len(created):.2f} total wagered!"

    return jsonify({"success": True, "message": msg, "count": len(created)})


@bet.route('/bets/parlay', methods=['POST'])
@login_required
def manual_parlay():
    """Place a manually-built parlay from the bet builder.

    Accepts JSON:
    {
        "stake": 25.0,
        "outcome": "pending",
        "legs": [
            {
                "team_a": "Lakers",
                "team_b": "Celtics",
                "match_date": "2026-02-25",
                "bet_type": "over",
                "over_under_line": 218.5,
                "player_name": "",
                "prop_type": "",
                "prop_line": null,
                "picked_team": "",
                "game_id": ""
            }
        ]
    }
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Invalid request"}), 400

    legs = data.get("legs", [])
    outcome = data.get("outcome", Outcome.PENDING.value)

    if not legs:
        return jsonify({"error": "Add at least one leg"}), 400

    try:
        stake = float(data.get("stake") or 0)
    except (TypeError, ValueError):
        return jsonify({"error": "Stake must be a number"}), 400
    if stake <= 0:
        return jsonify({"error": "Stake must be greater than zero"}), 400

    units_val = None
    if data.get("units") is not None:
        try:
            parsed_units = float(data.get("units"))
            if parsed_units > 0:
                units_val = parsed_units
        except (TypeError, ValueError):
            units_val = None
    parlay_id = Bet.generate_parlay_id()

    errors = []
    for i, leg in enumerate(legs):
        if not isinstance(leg, dict):
            errors.append(f"Leg {i + 1}: must be an object")
            continue
        if not leg.get("team_a") or not leg.get("team_b"):
            errors.append(f"Leg {i + 1}: team_a and team_b are required")
            continue

        try:
            match_date = datetime.strptime(leg.get("match_date", ""), "%Y-%m-%d")
        except ValueError:
            match_date = datetime.now(timezone.utc)

        bet_type = leg.get("bet_type", BetType.MONEYLINE.value)
        player_name = str(leg.get("player_name") or "")[:100] or None
        prop_type = str(leg.get("prop_type") or "")[:40] or None
        prop_line = None
        if leg.get("prop_line"):
            try:
                prop_line = float(leg["prop_line"])
            except (ValueError, TypeError):
                pass

        ou_line = None
        if bet_type in (BetType.OVER.value, BetType.UNDER.value) and not player_name:
            try:
                ou_line = float(leg["over_under_line"]) if leg.get("over_under_line") else None
            except (ValueError, TypeError):
                pass

        bet_obj = Bet(
            user_id=current_user.id,
            team_a=str(leg["team_a"])[:80],
            team_b=str(leg["team_b"])[:80],
            match_date=match_date,
            bet_amount=stake,
            units=units_val,
            outcome=outcome,
            bet_type=bet_type,
            over_under_line=ou_line,
            prop_line=prop_line,
            player_name=player_name,
            prop_type=prop_type,
            picked_team=str(leg.get("picked_team") or "")[:80] or None,
            external_game_id=leg.get("game_id") or None,
            is_parlay=True,
            parlay_id=parlay_id,
            source=BetSource.MANUAL.value,
        )
        db.session.add(bet_obj)

    if errors:
        db.session.rollback()
        return jsonify({"error": "; ".join(errors)}), 400

    db.session.commit()
    return jsonify({
        "success": True,
        "message": f"Parlay with {len(legs)} leg(s) saved — ${stake:.2f} wagered!",
        "redirect": url_for('bet.place_bet'),
    })


@bet.route('/nba/all-props')
@login_required
def nba_all_props():
    """Return a flat list of all player props across today's games for the prop browser."""
    games = get_todays_games()
    all_props = []
    player_team_cache: dict[str, str] = {}

    def _infer_player_team_abbr(player_name: str) -> str:
        cached = player_team_cache.get(player_name)
        if cached is not None:
            return cached
        team_abbr = ""
        try:
            player_id = find_player_id(player_name)
            if player_id:
                logs = get_cached_logs(player_id, last_n=1)
                if logs:
                    team_abbr = (logs[0].team_abbr or "").upper()
        except Exception:
            team_abbr = ""
        player_team_cache[player_name] = team_abbr
        return team_abbr

    for game in games:
        event_id = game.get('odds_event_id', '')
        if not event_id:
            continue
        props = fetch_player_props_for_event(event_id)
        team_a_abbr = (game.get('away', {}).get('abbr') or '').upper()
        team_b_abbr = (game.get('home', {}).get('abbr') or '').upper()
        for market_key, market_props in props.items():
            for prop in market_props:
                player_name = prop['player']
                player_team_abbr = _infer_player_team_abbr(player_name)
                if player_team_abbr and player_team_abbr == team_a_abbr:
                    player_team_name = game['away']['name']
                elif player_team_abbr and player_team_abbr == team_b_abbr:
                    player_team_name = game['home']['name']
                else:
                    player_team_name = ''
                all_props.append({
                    'player': player_name,
                    'market': market_key,
                    'line': prop['line'],
                    'over_odds': prop['over_odds'],
                    'under_odds': prop['under_odds'],
                    'game_id': game['espn_id'],
                    'team_a': game['away']['name'],
                    'team_b': game['home']['name'],
                    'team_a_abbr': team_a_abbr,
                    'team_b_abbr': team_b_abbr,
                    'player_team_abbr': player_team_abbr,
                    'player_team': player_team_name,
                    'match_date': game['start_time'][:10] if game.get('start_time') else '',
                })
    return jsonify(all_props)


def _parse_ocr_text(text: str) -> dict:
    """Parse raw OCR text from a bet screenshot into structured fields."""
    result: dict = {
        'player_name': None,
        'prop_type': None,
        'bet_type': None,
        'prop_line': None,
        'american_odds': None,
        'stake': None,
        'team_a': None,
        'team_b': None,
        'legs': [],
    }

    # Over / Under with a line number
    ou_match = re.search(r'\b(over|under)\s+([\d]+\.?\d*)\b', text, re.IGNORECASE)
    if ou_match:
        result['bet_type'] = ou_match.group(1).lower()
        result['prop_line'] = float(ou_match.group(2))

    # American odds (+/-NNN)
    odds_matches = re.findall(r'([+\-]\d{3,4})', text)
    if odds_matches:
        result['american_odds'] = int(odds_matches[0])

    # Dollar stake
    stake_matches = re.findall(r'\$\s*([\d]+\.?\d*)', text)
    if stake_matches:
        result['stake'] = float(stake_matches[0])

    # Matchup: "Team A @ Team B" or "Team A vs Team B"
    vs_match = re.search(
        r'([A-Za-z][A-Za-z\s]{2,25})\s+(?:@|vs\.?)\s+([A-Za-z][A-Za-z\s]{2,25})',
        text, re.IGNORECASE,
    )
    if vs_match:
        t1 = vs_match.group(1).strip()
        t2 = vs_match.group(2).strip()
        if 3 < len(t1) < 30 and 3 < len(t2) < 30:
            result['team_a'] = t1
            result['team_b'] = t2

    # Stat type detection
    stat_map = [
        (r'\bpoints?\b', 'player_points'),
        (r'\brebs?\b|\brebounds?\b', 'player_rebounds'),
        (r'\basts?\b|\bassists?\b', 'player_assists'),
        (r'\b3[- ]?pointers?\b|\bthrees?\b|\b3pts?\b', 'player_threes'),
        (r'\bblocks?\b|\bblks?\b', 'player_blocks'),
        (r'\bsteals?\b|\bstls?\b', 'player_steals'),
    ]
    for pattern, stat_type in stat_map:
        if re.search(pattern, text, re.IGNORECASE):
            result['prop_type'] = stat_type
            break

    # Player name: first line with two or more title-case words
    non_player = {
        'Over', 'Under', 'Game', 'Player', 'Total', 'Points', 'Rebounds',
        'Assists', 'Parlay', 'Bet', 'Same', 'Alternate', 'Combo', 'Spread',
    }
    for m in re.finditer(r'^([A-Z][a-z]+(?:\s+[A-Z][a-z\']+)+)', text, re.MULTILINE):
        candidate = m.group(1).strip()
        if candidate not in non_player and len(candidate.split()) >= 2:
            result['player_name'] = candidate
            break

    return result


@bet.route('/bets/ocr-screenshot', methods=['POST'])
@login_required
def ocr_screenshot():
    """Accept a PNG/JPG screenshot, OCR it, and return parsed bet fields as JSON."""
    if 'screenshot' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    file = request.files['screenshot']
    if not file or not file.filename:
        return jsonify({'error': 'No file selected'}), 400

    allowed_ext = ('.png', '.jpg', '.jpeg', '.webp', '.bmp')
    if not file.filename.lower().endswith(allowed_ext):
        return jsonify({'error': 'Only PNG/JPG/WEBP images are supported'}), 400

    try:
        from PIL import Image
        import pytesseract
    except ImportError:
        return jsonify({
            'error': (
                'OCR requires pytesseract + Pillow. '
                'Run: pip install pytesseract Pillow  '
                'and install the tesseract-ocr system package.'
            )
        }), 503

    try:
        img_bytes = file.read()
        img = Image.open(io.BytesIO(img_bytes))
        if img.mode not in ('RGB', 'L'):
            img = img.convert('RGB')

        # Upscale small images — OCR works better at ≥150 DPI equivalent
        w, h = img.size
        if w < 800:
            scale = 800 / w
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

        raw_text = pytesseract.image_to_string(img, config='--psm 3')
        parsed = _parse_ocr_text(raw_text)
        parsed['raw_text'] = raw_text[:3000]
        return jsonify({'success': True, **parsed})

    except Exception as exc:
        logger.error("OCR processing failed: %s", exc)
        return jsonify({'error': f'OCR failed: {exc}'}), 500


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


@bet.route('/bets/export')
@login_required
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


# ── NBA Prop Analysis ────────────────────────────────────────


@bet.route('/nba/analysis')
@login_required
def nba_analysis():
    """Display model-driven prop analysis with value detection."""
    engine = ProjectionEngine()
    detector = ValueDetector(engine)

    try:
        eligible_plays = detector.filter_plays(detector.score_all_todays_props(), min_edge=0.03)
        value_plays = eligible_plays[:50]
    except Exception as exc:
        logger.error("Analysis engine error: %s", exc)
        eligible_plays = []
        value_plays = []

    value_play_count = len(eligible_plays)
    strong_count = sum(1 for p in eligible_plays if p.get('confidence_tier') == 'strong')
    moderate_count = sum(1 for p in eligible_plays if p.get('confidence_tier') == 'moderate')
    games_count = len(set(p.get('game_id', '') for p in eligible_plays if p.get('game_id')))

    return render_template(
        'bets/nba_analysis.html',
        value_plays=value_plays,
        value_play_count=value_play_count,
        strong_count=strong_count,
        moderate_count=moderate_count,
        games_count=games_count,
    )


@bet.route('/nba/player-analysis/<player_name>')
@login_required
def nba_player_analysis(player_name):
    """Return detailed analysis data for a player as JSON (used by modal)."""
    prop_type = request.args.get('prop_type', 'player_points')

    player_id = find_player_id(player_name)
    if not player_id:
        return jsonify({'error': 'Player not found', 'game_log': [], 'breakdown': {}})

    logs = get_cached_logs(player_id, last_n=10)
    summary = get_player_stats_summary(player_id, logs)

    engine = ProjectionEngine()
    projection = engine.project_stat(player_name, prop_type)

    game_log = []
    for log in logs:
        game_log.append({
            'date': log.game_date.strftime('%b %d') if log.game_date else '',
            'matchup': log.matchup or '',
            'minutes': round(log.minutes or 0, 1),
            'pts': int(log.pts or 0),
            'reb': int(log.reb or 0),
            'ast': int(log.ast or 0),
            'fg3m': int(log.fg3m or 0),
        })

    return jsonify({
        'player': player_name,
        'prop_type': prop_type,
        'game_log': game_log,
        'summary': summary.get('season', {}),
        'breakdown': projection.get('breakdown', {}),
        'context_notes': projection.get('context_notes', []),
        'projection': projection.get('projection', 0),
        'std_dev': projection.get('std_dev', 0),
        'z_score': projection.get('z_score', 0),
        'games_played': projection.get('games_played', 0),
        'projection_source': projection.get('projection_source', 'heuristic'),
    })
