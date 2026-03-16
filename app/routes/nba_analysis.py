"""NBA prop analysis routes: all-props browser, analysis dashboard, player detail, stat analysis."""

import logging
import re
from collections import defaultdict
from datetime import date as date_type

from flask import request, jsonify, render_template
from flask_login import login_required

from app.config_display import PROP_STAT_KEY
from app.models import GameSnapshot, OddsSnapshot, PlayerGameLog, TeamDefenseSnapshot
from app.services.nba_service import (
    get_todays_games,
    fetch_upcoming_games,
    fetch_player_props_for_event,
)
from app.services.projection_engine import ProjectionEngine
from app.services.stats_service import find_player_id, get_cached_logs, get_player_stats_summary
from app.routes.nba_live import _build_stat_context
from app.routes.bet_import import _POSITION_ORDER

logger = logging.getLogger(__name__)

_STAT_COL = PROP_STAT_KEY


# ── Helpers ───────────────────────────────────────────────────────────────

def _normalize_name(value: str) -> str:
    return re.sub(r'[^a-z0-9]+', ' ', (value or '').lower()).strip()


def _hit_rates_from_logs(logs: list, col_name: str | None, line: float) -> dict:
    """Compute hit rates from pre-fetched PlayerGameLog rows (no DB query)."""
    if not col_name or not logs:
        return {'over_pct': None, 'under_pct': None, 'games': [], 'sample': 0}
    games = []
    for g in logs:
        val = getattr(g, col_name, None)
        if val is None:
            continue
        fval = float(val)
        games.append({'date': str(g.game_date), 'value': round(fval, 1),
                      'matchup': g.matchup or '',
                      'result': 'over' if fval >= line else 'under'})
    if not games:
        return {'over_pct': None, 'under_pct': None, 'games': [], 'sample': 0}
    over_count = sum(1 for g in games if g['result'] == 'over')
    sample = len(games)
    return {'over_pct': round(over_count / sample * 100),
            'under_pct': round((sample - over_count) / sample * 100),
            'games': games[:10], 'sample': sample}


def _compute_hit_rates(player_name: str, prop_type: str, line: float, n: int = 20) -> dict:
    """Fetch logs for a single player and compute hit rates."""
    col_name = _STAT_COL.get(prop_type)
    if not col_name:
        return {'over_pct': None, 'under_pct': None, 'games': [], 'sample': 0}
    col = getattr(PlayerGameLog, col_name)
    logs = (PlayerGameLog.query.filter_by(player_name=player_name)
            .filter(col.isnot(None))
            .order_by(PlayerGameLog.game_date.desc()).limit(n).all())
    return _hit_rates_from_logs(logs, col_name, line)


def _resolve_player_team_abbrs(player_names: set[str]) -> dict[str, str]:
    """Resolve latest team abbreviation for each player from cache (with fallback lookup)."""
    if not player_names:
        return {}

    resolved: dict[str, str] = {}
    rows = (
        PlayerGameLog.query
        .filter(PlayerGameLog.player_name.in_(list(player_names)))
        .order_by(PlayerGameLog.player_name, PlayerGameLog.game_date.desc())
        .all()
    )
    for row in rows:
        if row.player_name not in resolved and row.team_abbr:
            resolved[row.player_name] = (row.team_abbr or "").upper()

    for player_name in player_names:
        if player_name in resolved:
            continue
        try:
            player_id = find_player_id(player_name)
            if not player_id:
                continue
            logs = get_cached_logs(player_id, last_n=1)
            if logs and logs[0].team_abbr:
                resolved[player_name] = (logs[0].team_abbr or "").upper()
        except Exception:
            continue

    return resolved


# ── Routes ────────────────────────────────────────────────────────────────

@login_required
def nba_all_props():
    """Return a flat list of all player props across today's games."""
    today = date_type.today()

    raw_props = []
    player_names: set[str] = set()

    def _append_props_for_games(games_batch: list[dict]) -> None:
        for game in games_batch:
            event_id = game.get('odds_event_id', '')
            if not event_id:
                continue
            props = fetch_player_props_for_event(event_id)
            if not isinstance(props, dict):
                continue
            away = game.get('away', {}) or {}
            home = game.get('home', {}) or {}
            team_a_abbr = (away.get('abbr') or '').upper()
            team_b_abbr = (home.get('abbr') or '').upper()
            for market_key, market_props in props.items():
                for prop in market_props:
                    player_name = prop.get('player')
                    if not player_name:
                        continue
                    player_names.add(player_name)
                    raw_props.append({
                        'player': player_name,
                        'market': market_key,
                        'line': prop.get('line'),
                        'over_odds': prop.get('over_odds'),
                        'under_odds': prop.get('under_odds'),
                        'books': prop.get('books', {}),
                        'best_over_book': prop.get('best_over_book', ''),
                        'best_under_book': prop.get('best_under_book', ''),
                        'game_id': game.get('espn_id', ''),
                        'team_a': away.get('name', ''),
                        'team_b': home.get('name', ''),
                        'team_a_abbr': team_a_abbr,
                        'team_b_abbr': team_b_abbr,
                        'match_date': (game.get('start_time', '') or game.get('match_date', ''))[:10],
                    })

    _append_props_for_games(get_todays_games())

    if not raw_props:
        _append_props_for_games(fetch_upcoming_games())

    if not raw_props:
        try:
            game_rows = GameSnapshot.query.filter_by(game_date=today).all()
            game_map = {g.game_id: g for g in game_rows}

            latest_by_key: dict = {}
            snap_rows = (
                OddsSnapshot.query
                .filter_by(game_date=today)
                .order_by(OddsSnapshot.snapped_at.desc())
                .all()
            )
            for snap in snap_rows:
                key = (snap.game_id, snap.player_name, snap.market)
                slot = latest_by_key.setdefault(key, {'books': {}})
                if snap.bookmaker and snap.bookmaker not in slot['books']:
                    slot['books'][snap.bookmaker] = {
                        'line': snap.line,
                        'over_odds': snap.over_odds,
                        'under_odds': snap.under_odds,
                    }

            for (game_id, player_name, market), slot in latest_by_key.items():
                books = slot.get('books', {})
                if not books:
                    continue
                preferred_book = 'fanduel' if 'fanduel' in books else next(iter(books.keys()))
                preferred = books.get(preferred_book) or {}
                over_choice = max(
                    ((bk, data.get('over_odds')) for bk, data in books.items() if data.get('over_odds') is not None),
                    key=lambda x: x[1],
                    default=('', None),
                )
                under_choice = max(
                    ((bk, data.get('under_odds')) for bk, data in books.items() if data.get('under_odds') is not None),
                    key=lambda x: x[1],
                    default=('', None),
                )
                game_row = game_map.get(game_id)
                raw_props.append({
                    'player': player_name,
                    'market': market,
                    'line': preferred.get('line'),
                    'over_odds': preferred.get('over_odds'),
                    'under_odds': preferred.get('under_odds'),
                    'books': books,
                    'best_over_book': over_choice[0] or '',
                    'best_under_book': under_choice[0] or '',
                    'game_id': game_id or '',
                    'team_a': (game_row.away_team if game_row else '') or '',
                    'team_b': (game_row.home_team if game_row else '') or '',
                    'team_a_abbr': '',
                    'team_b_abbr': '',
                    'match_date': today.isoformat(),
                })
                player_names.add(player_name)
        except Exception as exc:
            logger.warning("nba_all_props snapshot fallback failed: %s", exc)

    movement_map: dict = {}
    try:
        snapshots = OddsSnapshot.query.filter_by(game_date=today).order_by(OddsSnapshot.snapped_at).all()
        for snap in snapshots:
            key = (snap.game_id, snap.player_name, snap.market)
            if key not in movement_map:
                movement_map[key] = snap.line
    except Exception as exc:
        logger.warning("Failed to load OddsSnapshot movement data: %s", exc)

    player_team_map = _resolve_player_team_abbrs(player_names)
    all_props = []
    for prop in raw_props:
        player_team_abbr = player_team_map.get(prop['player'], '')
        if player_team_abbr and player_team_abbr == prop.get('team_a_abbr', ''):
            player_team_name = prop.get('team_a', '')
        elif player_team_abbr and player_team_abbr == prop.get('team_b_abbr', ''):
            player_team_name = prop.get('team_b', '')
        else:
            player_team_name = ''

        enriched = dict(prop)
        enriched['player_team_abbr'] = player_team_abbr
        enriched['player_team'] = player_team_name

        mv_key = (prop['game_id'], prop['player'], prop['market'])
        first_line = movement_map.get(mv_key)
        if first_line is not None and first_line != prop['line']:
            delta = round(prop['line'] - first_line, 2)
            enriched['movement'] = {
                'line_delta': delta,
                'direction': 'up' if delta > 0 else 'down',
                'first_line': first_line,
            }
        else:
            enriched['movement'] = {'line_delta': 0, 'direction': 'flat', 'first_line': prop['line']}

        all_props.append(enriched)

    return jsonify(all_props)


@login_required
def nba_analysis():
    """Display model-driven prop analysis with value detection."""
    from app.services.score_cache import get_todays_scores
    from app.services.value_detector import ValueDetector

    try:
        all_scores = get_todays_scores()
        eligible_plays = ValueDetector.filter_plays(all_scores, min_edge=0.03)
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


@login_required
def nba_stat_analysis():
    """Display today's props grouped by matchup with a slide-in detail panel."""
    from app.services.score_cache import get_todays_scores

    try:
        scores = get_todays_scores()
    except Exception as exc:
        logger.error("Stat analysis engine error: %s", exc)
        scores = []

    games_today = get_todays_games()

    game_lookup = {g.get('espn_id'): g for g in games_today}

    all_player_names = {s.get('player', '') for s in scores if s.get('player')}
    _all_logs = (
        PlayerGameLog.query
        .filter(PlayerGameLog.player_name.in_(list(all_player_names)))
        .order_by(PlayerGameLog.game_date.desc())
        .all()
    )
    logs_by_player: dict[str, list] = defaultdict(list)
    for _log in _all_logs:
        if len(logs_by_player[_log.player_name]) < 20:
            logs_by_player[_log.player_name].append(_log)

    player_team_abbr_map: dict[str, str] = {}
    for _pname, _plogs in logs_by_player.items():
        for _plog in _plogs:
            if _plog.team_abbr:
                player_team_abbr_map[_pname] = _plog.team_abbr.upper()
                break

    for s in scores:
        if not s.get('player_team_abbr'):
            s['player_team_abbr'] = player_team_abbr_map.get(s.get('player', ''), '')

    _opp_abbrs: set[str] = set()
    for s in scores:
        _game = game_lookup.get(s.get('game_id'), {})
        _pt = (s.get('player_team_abbr') or '').upper()
        _home = (_game.get('home') or {}).get('abbr', '').upper()
        _away = (_game.get('away') or {}).get('abbr', '').upper()
        _opp = _away if _pt == _home else _home
        if _opp:
            _opp_abbrs.add(_opp)
    _def_rows = (
        TeamDefenseSnapshot.query
        .filter(TeamDefenseSnapshot.team_abbr.in_(list(_opp_abbrs)))
        .order_by(TeamDefenseSnapshot.fetched_at.desc())
        .all()
    )
    def_snap_map: dict[str, TeamDefenseSnapshot] = {}
    for _snap in _def_rows:
        if _snap.team_abbr not in def_snap_map:
            def_snap_map[_snap.team_abbr] = _snap

    for s in scores:
        line = float(s.get('line') or 0)
        col_name = _STAT_COL.get(s.get('prop_type', ''))
        s['hit_rates'] = _hit_rates_from_logs(logs_by_player.get(s.get('player', ''), []), col_name, line)
        s['game_ctx'] = _build_stat_context(s, game_lookup, def_snap_map)
        tier = s.get('confidence_tier', 'no_edge')
        wp = s.get('win_probability') or 0.5
        if tier == 'strong':
            s['indicator'] = 'strong'
        elif tier == 'moderate':
            s['indicator'] = 'value'
        elif tier == 'slight':
            s['indicator'] = 'slight'
        else:
            s['indicator'] = 'avoid'
        if wp < 0.40 and s['indicator'] != 'avoid':
            s['indicator'] = 'avoid'

    game_map = {}
    for g in games_today:
        gid = g.get('espn_id')
        game_map[gid] = {'meta': g, 'home': [], 'away': []}

    for s in scores:
        gid = s.get('game_id')
        if gid not in game_map:
            continue
        pt = (s.get('player_team_abbr') or '').upper()
        home_abbr = (game_map[gid]['meta'].get('home') or {}).get('abbr', '').upper()
        bucket = 'home' if pt == home_abbr else 'away'
        game_map[gid][bucket].append(s)

    for gdata in game_map.values():
        for bucket in ('home', 'away'):
            gdata[bucket].sort(
                key=lambda s: _POSITION_ORDER.get(
                    (s.get('breakdown') or {}).get('player_position', ''), 99))

    matchups = [v for v in game_map.values() if v['home'] or v['away']]

    stat_filter = request.args.get('stat', 'all')
    search_q = request.args.get('q', '').strip().lower()
    if stat_filter != 'all' or search_q:
        for m in matchups:
            for bucket in ('home', 'away'):
                m[bucket] = [s for s in m[bucket]
                             if (stat_filter == 'all' or s.get('prop_type') == stat_filter)
                             and (not search_q or search_q in (s.get('player') or '').lower())]

    total = sum(len(m['home']) + len(m['away']) for m in matchups)
    strong_ct = sum(1 for m in matchups for s in m['home'] + m['away'] if s.get('indicator') == 'strong')
    value_ct = sum(1 for m in matchups for s in m['home'] + m['away'] if s.get('indicator') == 'value')
    avoid_ct = sum(1 for m in matchups for s in m['home'] + m['away'] if s.get('indicator') == 'avoid')

    return render_template('bets/nba_stat_analysis.html',
                           matchups=matchups,
                           stat_filter=stat_filter,
                           search_q=search_q,
                           total=total, strong_ct=strong_ct,
                           value_ct=value_ct, avoid_ct=avoid_ct)
