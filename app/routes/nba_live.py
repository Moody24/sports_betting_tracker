"""NBA live-data routes: today's games, prop progress, betslip placement."""

import json
import logging
import re
import time
from datetime import datetime, timedelta

from flask import render_template, redirect, url_for, flash, request, jsonify, current_app
from flask_login import login_required, current_user
import requests

from app import db
from app.config_display import PROP_ESPN_COLUMN, PROP_TO_OPP_ALLOWED
from app.enums import BetSource, BetType, Outcome
from app.models import Bet, GameSnapshot, TeamDefenseSnapshot
from app.utils import safe_float
from app.services.nba_service import (
    get_todays_games,
    fetch_upcoming_games,
    fetch_player_props_for_event,
    resolve_pending_bets,
    get_player_props,
    resolve_card_progress as _resolve_card_progress,
    ESPN_SUMMARY_URL,
    APP_TIMEZONE as NBA_APP_TIMEZONE,
)
from app.services.market_recommender import recommend_market_sides
from app.services.projection_engine import ProjectionEngine
from app.services.value_detector import ValueDetector
from app.services.postmortem_service import create_or_update_postmortem
from app.routes.bet_crud import _create_pick_context_for_bet

logger = logging.getLogger(__name__)

# ── Module-level caches ────────────────────────────────────────────────────
_PROP_PROGRESS_CACHE: dict[tuple, dict] = {}
_PROP_PROGRESS_TTL_SECONDS = 30
_PROP_PROGRESS_CACHE_MAX = 2000

# Raw ESPN summary cache: espn_id → {data, expires_at}
_GAME_SUMMARY_CACHE: dict[str, dict] = {}
_GAME_SUMMARY_TTL = 30

# Snapshot live-score write debounce: only commit live score updates once per
# _SNAPSHOT_WRITE_TTL seconds per game.
_SNAPSHOT_WRITE_TS: dict[str, float] = {}
_SNAPSHOT_WRITE_TTL = 60


# ── Helpers ───────────────────────────────────────────────────────────────

def _normalize_name(value: str) -> str:
    return re.sub(r'[^a-z0-9]+', ' ', (value or '').lower()).strip()


def _build_stat_context(score: dict, games_today, def_snap_map: dict | None = None) -> dict:
    """Build defensive + game context for a prop score."""
    ctx = {}
    if isinstance(games_today, dict):
        game = games_today.get(score.get('game_id'), {})
    else:
        game = next((g for g in games_today if g.get('espn_id') == score.get('game_id')), {})

    ctx['over_under_line'] = game.get('over_under_line')
    ctx['moneyline_home'] = game.get('moneyline_home')
    ctx['moneyline_away'] = game.get('moneyline_away')
    ml_h = game.get('moneyline_home') or 0
    ml_a = game.get('moneyline_away') or 0
    ctx['blowout_risk'] = abs(ml_h) >= 400 or abs(ml_a) >= 400

    player_team = score.get('player_team_abbr') or ''
    home_abbr = (game.get('home') or {}).get('abbr', '')
    away_abbr = (game.get('away') or {}).get('abbr', '')
    opp_abbr = away_abbr if player_team == home_abbr else home_abbr
    ctx['opp_abbr'] = opp_abbr

    if def_snap_map is not None:
        def_snap = def_snap_map.get(opp_abbr) if opp_abbr else None
    else:
        def_snap = (TeamDefenseSnapshot.query.filter_by(team_abbr=opp_abbr)
                    .order_by(TeamDefenseSnapshot.fetched_at.desc()).first()) if opp_abbr else None

    if def_snap:
        ctx['opp_def_rating'] = def_snap.def_rating
        ctx['opp_pace'] = def_snap.pace
        opp_field = PROP_TO_OPP_ALLOWED.get(score.get('prop_type', ''))
        ctx['opp_stat_allowed'] = getattr(def_snap, opp_field, None) if opp_field else None
        position = (score.get('breakdown') or {}).get('player_position', '')
        pos_map = {'PG': def_snap.opp_pts_allowed_pg, 'SG': def_snap.opp_pts_allowed_sg,
                   'SF': def_snap.opp_pts_allowed_sf, 'PF': def_snap.opp_pts_allowed_pf,
                   'C': def_snap.opp_pts_allowed_c}
        ctx['opp_pos_allowed'] = pos_map.get(position)
        ctx['player_position'] = position
    else:
        ctx.update(opp_def_rating=None, opp_pace=None, opp_stat_allowed=None,
                   opp_pos_allowed=None, player_position='')
    return ctx


def _extract_prop_boxscore(summary_data: dict) -> dict:
    """Extract prop-relevant player stats from ESPN summary payload."""
    stat_column_map = PROP_ESPN_COLUMN
    player_stats: dict = {}
    for team_block in summary_data.get("boxscore", {}).get("players", []):
        for stat_block in team_block.get("statistics", []):
            column_names: list[str] = stat_block.get("names", [])
            for athlete in stat_block.get("athletes", []):
                name = athlete.get("athlete", {}).get("displayName", "")
                if not name:
                    continue
                raw_stats: list[str] = athlete.get("stats", [])
                entry: dict = {}
                for prop_type, col_header in stat_column_map.items():
                    if col_header not in column_names:
                        continue
                    idx = column_names.index(col_header)
                    if idx >= len(raw_stats):
                        continue
                    raw = raw_stats[idx]
                    if "-" in str(raw):
                        raw = str(raw).split("-")[0]
                    try:
                        entry[prop_type] = float(raw)
                    except (ValueError, TypeError):
                        continue
                if entry:
                    entry["player_points_rebounds_assists"] = (
                        float(entry.get("player_points", 0) or 0)
                        + float(entry.get("player_rebounds", 0) or 0)
                        + float(entry.get("player_assists", 0) or 0)
                    )
                    player_stats[name] = entry
    return player_stats


def _clock_to_seconds(clock_value: str) -> int:
    if not clock_value or ':' not in str(clock_value):
        return 0
    parts = str(clock_value).split(':')
    if len(parts) != 2:
        return 0
    try:
        return int(parts[0]) * 60 + int(parts[1])
    except (TypeError, ValueError):
        return 0


def _estimate_elapsed_ratio(period: int | None, clock: str, game_state: str) -> float:
    total_seconds = 48 * 60
    if game_state == 'final':
        return 1.0
    if game_state == 'pregame':
        return 0.0

    p = max(1, int(period or 1))
    period_elapsed = 12 * 60 - _clock_to_seconds(clock)
    elapsed = (p - 1) * 12 * 60 + max(0, min(12 * 60, period_elapsed))
    return max(0.0, min(1.0, elapsed / total_seconds))


def _derive_game_status(summary_data: dict) -> dict:
    status_type = (
        summary_data.get('header', {})
        .get('competitions', [{}])[0]
        .get('status', {})
        .get('type', {})
    )
    short_detail = status_type.get('shortDetail', '')
    detail = status_type.get('detail') or status_type.get('description') or short_detail or 'Status unavailable'
    status_name = (status_type.get('name') or '').upper()
    status_text = f"{status_name}: {detail}".strip(': ').strip()
    period = int(status_type.get('period') or 0) if str(status_type.get('period') or '').isdigit() else 0
    clock = status_type.get('displayClock') or ''

    if status_name in {'STATUS_FINAL', 'FINAL'}:
        game_state = 'final'
    elif status_name in {'STATUS_SCHEDULED', 'STATUS_PRE'}:
        game_state = 'pregame'
    elif 'HALFTIME' in status_name:
        game_state = 'halftime'
    else:
        game_state = 'live'

    elapsed_ratio = _estimate_elapsed_ratio(period, clock, game_state)
    return {
        'status_text': status_text,
        'period': period,
        'clock': clock,
        'game_state': game_state,
        'elapsed_ratio': elapsed_ratio,
    }


def _prune_prop_progress_cache(now_monotonic: float) -> None:
    expired_keys = [k for k, v in _PROP_PROGRESS_CACHE.items() if v.get("expires_at", 0) <= now_monotonic]
    for key in expired_keys:
        _PROP_PROGRESS_CACHE.pop(key, None)

    if len(_PROP_PROGRESS_CACHE) <= _PROP_PROGRESS_CACHE_MAX:
        return

    survivors = sorted(_PROP_PROGRESS_CACHE.items(), key=lambda kv: kv[1].get("created_at", 0), reverse=True)
    keep = survivors[: _PROP_PROGRESS_CACHE_MAX // 2]
    _PROP_PROGRESS_CACHE.clear()
    _PROP_PROGRESS_CACHE.update(dict(keep))


def _get_game_summary(espn_id: str, now_monotonic: float) -> dict:
    """Return cached ESPN summary JSON for *espn_id*, fetching if expired/absent."""
    cached = _GAME_SUMMARY_CACHE.get(espn_id)
    if cached and cached.get('expires_at', 0) > now_monotonic:
        return cached['data']
    try:
        resp = requests.get(ESPN_SUMMARY_URL, params={'event': espn_id}, timeout=8)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        logger.warning("Live data fetch failed — returning empty result", exc_info=True)
        data = {}
    _GAME_SUMMARY_CACHE[espn_id] = {'data': data, 'expires_at': now_monotonic + _GAME_SUMMARY_TTL}
    return data


# ── Routes ────────────────────────────────────────────────────────────────

@login_required
def nba_today():
    games = get_todays_games()
    upcoming_games = fetch_upcoming_games()
    today = datetime.now(NBA_APP_TIMEZONE).date()

    espn_ids = [g['espn_id'] for g in games]
    existing_snaps = (
        GameSnapshot.query
        .filter(GameSnapshot.espn_id.in_(espn_ids), GameSnapshot.game_date == today)
        .all()
    ) if espn_ids else []
    snap_map = {s.espn_id: s for s in existing_snaps}

    now_mono = time.monotonic()
    for game in games:
        snap = snap_map.get(game['espn_id'])
        eid = game['espn_id']

        if snap is None:
            snap = GameSnapshot(
                espn_id=eid,
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
            _SNAPSHOT_WRITE_TS[eid] = now_mono
        elif now_mono - _SNAPSHOT_WRITE_TS.get(eid, 0) >= _SNAPSHOT_WRITE_TTL:
            snap.home_score = game['home']['score']
            snap.away_score = game['away']['score']
            snap.status = game['status']
            if game['status'] == 'STATUS_FINAL':
                snap.is_final = True
            if not snap.home_logo:
                snap.home_logo = game['home'].get('logo', '')
            if not snap.away_logo:
                snap.away_logo = game['away'].get('logo', '')
            _SNAPSHOT_WRITE_TS[eid] = now_mono

        if snap.props_json is None:
            event_id = (game.get('odds_event_id') or '').strip()
            if event_id:
                props = fetch_player_props_for_event(event_id)
                if props:
                    snap.props_json = json.dumps(props)

    if db.session.new or db.session.dirty:
        db.session.commit()

    active_games = [g for g in games if g['status'] != 'STATUS_FINAL']
    market_recs = {}
    try:
        market_recs = recommend_market_sides(active_games)
    except Exception as exc:
        logger.debug('Market recommendations unavailable: %s', exc)
    yesterday = today - timedelta(days=1)
    completed_snaps = (
        GameSnapshot.query
        .filter(GameSnapshot.is_final.is_(True))
        .filter(GameSnapshot.game_date >= yesterday)
        .order_by(GameSnapshot.game_date.desc(), GameSnapshot.snapshot_time)
        .all()
    )

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
        market_recs=market_recs,
    )


@login_required
def nba_update_results():
    pending = Bet.query.filter_by(
        user_id=current_user.id, outcome=Outcome.PENDING.value
    ).all()

    resolved = resolve_pending_bets(pending)
    count = 0
    for bet_obj, outcome, actual_value in resolved:
        bet_obj.outcome = outcome
        bet_obj.actual_total = actual_value
        count += 1

    if count:
        db.session.commit()
        for bet_obj, outcome, _actual in resolved:
            try:
                create_or_update_postmortem(bet_obj)
            except Exception:
                logger.exception("Postmortem failed for bet_id=%s", bet_obj.id)
        flash(f'Updated {count} bet(s) with final results.', 'success')
    else:
        flash('No pending bets could be resolved yet.', 'info')

    return redirect(request.referrer or url_for('bet.place_bet'))


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
            'moneyline_away': g.get('moneyline_away'),
            'moneyline_home': g.get('moneyline_home'),
        })
    for g in tomorrow_games:
        results.append({
            'label': f"{g['away']['name']} @ {g['home']['name']} (Tomorrow)",
            'team_a': g['away']['name'],
            'team_b': g['home']['name'],
            'match_date': g.get('match_date', ''),
            'game_id': g['espn_id'],
            'over_under_line': g.get('over_under_line'),
            'moneyline_away': g.get('moneyline_away'),
            'moneyline_home': g.get('moneyline_home'),
        })

    return jsonify(results)


@login_required
def nba_props(espn_id):
    """Return player props for a game as JSON and persist them to snapshot."""
    if not re.match(r'^[A-Za-z0-9_-]+$', str(espn_id)):
        return jsonify({"success": False, "message": "Invalid espn_id format"}), 400
    today = datetime.now(NBA_APP_TIMEZONE).date()
    snap = GameSnapshot.query.filter_by(espn_id=espn_id, game_date=today).first()
    if snap and snap.props_json:
        try:
            return jsonify(json.loads(snap.props_json))
        except (TypeError, ValueError):
            pass

    props = get_player_props(espn_id)

    if snap and snap.props_json is None and props:
        snap.props_json = json.dumps(props)
        db.session.commit()

    return jsonify(props)


@login_required
def nba_prop_progress(espn_id):
    if not re.match(r'^[A-Za-z0-9_-]+$', str(espn_id)):
        return jsonify({"success": False, "message": "Invalid espn_id format"}), 400
    player_name = (request.args.get('player') or '').strip()
    prop_type = (request.args.get('prop_type') or '').strip()
    if not player_name or not prop_type:
        return jsonify({'ok': False, 'error': 'player and prop_type are required'}), 400

    line = safe_float(request.args.get('line'), 0.0)
    bet_type = (request.args.get('bet_type') or '').strip().lower()

    use_cache = not current_app.testing
    cache_key = (
        espn_id,
        _normalize_name(player_name),
        prop_type,
        bet_type,
        round(line, 2),
    )
    now_monotonic = time.monotonic()
    if use_cache:
        _prune_prop_progress_cache(now_monotonic)
        cached = _PROP_PROGRESS_CACHE.get(cache_key)
        if cached and cached.get('expires_at', 0) > now_monotonic:
            return jsonify(cached['payload'])

    summary_data = _get_game_summary(espn_id, now_monotonic) if use_cache else {}
    if not use_cache:
        try:
            resp = requests.get(ESPN_SUMMARY_URL, params={'event': espn_id}, timeout=8)
            resp.raise_for_status()
            summary_data = resp.json()
        except Exception:
            logger.warning("Summary fetch failed for espn_id=%s — returning empty", espn_id, exc_info=True)
            summary_data = {}

    if not summary_data:
        payload = {'ok': False, 'status': 'game_not_started', 'error': 'No boxscore data available yet'}
        if use_cache:
            _PROP_PROGRESS_CACHE[cache_key] = {'expires_at': now_monotonic + _PROP_PROGRESS_TTL_SECONDS,
                                               'created_at': now_monotonic, 'payload': payload}
        return jsonify(payload), 200

    payload = _resolve_card_progress(espn_id, player_name, prop_type, line, bet_type, summary_data)
    if use_cache:
        _PROP_PROGRESS_CACHE[cache_key] = {'expires_at': now_monotonic + _PROP_PROGRESS_TTL_SECONDS,
                                           'created_at': now_monotonic, 'payload': payload}

    if not payload.get('ok'):
        status_code = 404 if 'not found' in payload.get('error', '') or 'unavailable' in payload.get('error', '') else 200
        return jsonify(payload), status_code
    return jsonify(payload)


@login_required
def nba_prop_progress_batch():
    """Batch live-progress endpoint — resolves N cards with one ESPN call per unique game."""
    body = request.get_json(silent=True) or []
    if not isinstance(body, list) or not body:
        return jsonify({'ok': False, 'error': 'Expected non-empty JSON array'}), 400

    use_cache = not current_app.testing
    now_monotonic = time.monotonic()
    if use_cache:
        _prune_prop_progress_cache(now_monotonic)

    by_game: dict[str, list] = {}
    for item in body:
        eid = str(item.get('espn_id') or '')
        if eid:
            by_game.setdefault(eid, []).append(item)

    results: dict[str, dict] = {}
    for espn_id, items in by_game.items():
        summary_data = None

        for item in items:
            card_id = str(item.get('card_id') or '')
            player_name = (item.get('player') or '').strip()
            prop_type = (item.get('prop_type') or '').strip()
            line = safe_float(item.get('line'), 0.0)
            bet_type = (item.get('bet_type') or '').strip().lower()

            if not card_id or not player_name or not prop_type:
                if card_id:
                    results[card_id] = {'ok': False, 'error': 'Missing required fields'}
                continue

            cache_key = (espn_id, _normalize_name(player_name), prop_type, bet_type, round(line, 2))
            if use_cache:
                cached = _PROP_PROGRESS_CACHE.get(cache_key)
                if cached and cached.get('expires_at', 0) > now_monotonic:
                    results[card_id] = cached['payload']
                    continue

            if summary_data is None:
                summary_data = _get_game_summary(espn_id, now_monotonic) if use_cache else {}
                if not use_cache:
                    try:
                        resp = requests.get(ESPN_SUMMARY_URL, params={'event': espn_id}, timeout=8)
                        resp.raise_for_status()
                        summary_data = resp.json()
                    except Exception:
                        logger.warning("Summary fetch failed — returning empty", exc_info=True)
                        summary_data = {}

            if not summary_data:
                payload = {'ok': False, 'status': 'game_not_started', 'error': 'No boxscore data available yet'}
            else:
                payload = _resolve_card_progress(espn_id, player_name, prop_type, line, bet_type, summary_data)

            results[card_id] = payload
            if use_cache:
                _PROP_PROGRESS_CACHE[cache_key] = {'expires_at': now_monotonic + _PROP_PROGRESS_TTL_SECONDS,
                                                   'created_at': now_monotonic, 'payload': payload}

    return jsonify(results)


@login_required
def nba_place_bets():
    """Place one or more prop bets from the bet slip."""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"success": False, "message": "Invalid request"}), 400

    legs = data.get("legs", [])
    is_parlay = bool(data.get("is_parlay", False))

    rr_payload = data.get("round_robin")
    rr_size = None
    if rr_payload and isinstance(rr_payload, dict):
        try:
            rr_size = int(rr_payload.get("size") or 0) or None
        except (TypeError, ValueError):
            rr_size = None

    if not legs:
        return jsonify({"success": False, "message": "No selections provided"}), 400

    try:
        stake = float(data.get("stake") or 0)
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "Stake must be a number"}), 400
    if stake <= 0:
        return jsonify({"success": False, "message": "Stake must be greater than zero"}), 400

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
            from datetime import timezone
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
            over_under_line_val = prop_line_val
        picked_team_val = str(leg.get("picked_team") or "")[:80] or None

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
            picked_team=picked_team_val if leg.get("bet_type") == BetType.MONEYLINE.value else None,
            american_odds=american_odds_val,
            external_game_id=leg.get("game_id") or None,
            player_name=player_name_val,
            prop_type=prop_type_val,
            prop_line=prop_line_val if is_player_prop else None,
            is_parlay=is_parlay,
            parlay_id=parlay_id,
            source=BetSource.NBA_PROPS.value,
            bonus_multiplier=bonus_mult,
            round_robin_size=rr_size,
            parlay_group_id=None,  # set below for RR bets
        )
        db.session.add(bet_obj)
        created.append(bet_obj)

    if errors:
        db.session.rollback()
        return jsonify({"success": False, "message": "; ".join(errors)}), 400

    if rr_size and len(created) >= rr_size:
        import uuid
        rr_group_id = str(uuid.uuid4())[:40]
        for leg_obj in created:
            leg_obj.parlay_group_id = rr_group_id

    db.session.flush()
    detector = ValueDetector(ProjectionEngine())
    for bet_obj in created:
        _create_pick_context_for_bet(
            bet_obj=bet_obj,
            detector=detector,
            selected_odds=bet_obj.american_odds,
        )

    db.session.commit()

    if is_parlay and parlay_id and created:
        leg_count = len(created)
        for leg_obj in created:
            leg_obj.parlay_leg_count = leg_count
        db.session.commit()

    if is_parlay:
        msg = f"Parlay with {len(created)} leg(s) placed — ${stake:.2f} wagered!"
    else:
        msg = f"{len(created)} bet(s) placed — ${stake * len(created):.2f} total wagered!"

    return jsonify({"success": True, "message": msg, "count": len(created)})
