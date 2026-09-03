"""NBA prop analysis routes: all-props browser, analysis dashboard, player detail, stat analysis."""

import logging
import re
from collections import defaultdict
from datetime import date as date_type

from flask import request, jsonify, render_template
from flask_login import login_required

from app import limiter

from app.config_display import PROP_STAT_KEY
from app.models import GameSnapshot, OddsSnapshot, PlayerGameLog, TeamDefenseSnapshot
from app.services.nba_service import (
    get_todays_games,
    fetch_upcoming_games,
    fetch_player_props_for_event,
)
from app.services.projection_engine import ProjectionEngine
from app.services.stats_service import find_player_id, get_cached_logs, get_player_stats_summary
from app.services.analysis_context import POSITION_ORDER, build_stat_context
from app.utils.odds import american_from_decimal

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


def _analysis_play_view_models(plays: list[dict]) -> list[dict]:
    """Copy and enrich analysis plays for the probability-first board.

    Recent form is loaded in one query for every visible player. The cached
    scored-prop mappings remain untouched so a page render cannot leak
    presentation-only fields into other consumers of the shared cache.
    """
    view_models = [dict(play) for play in plays]
    player_names = {play.get('player') for play in view_models if play.get('player')}

    logs_by_player: dict[str, list[PlayerGameLog]] = defaultdict(list)
    if player_names:
        logs = (
            PlayerGameLog.query
            .filter(PlayerGameLog.player_name.in_(list(player_names)))
            .order_by(PlayerGameLog.player_name, PlayerGameLog.game_date.desc())
            .all()
        )
        for log in logs:
            if len(logs_by_player[log.player_name]) < 7:
                logs_by_player[log.player_name].append(log)

    for play in view_models:
        side = (play.get('recommended_side') or 'over').lower()
        probability_key = 'model_prob_under' if side == 'under' else 'model_prob_over'
        probability = play.get(probability_key)
        if probability is None:
            probability = play.get('win_probability')
        try:
            probability = min(max(float(probability), 0.0), 1.0)
        except (TypeError, ValueError):
            probability = 0.5

        play['side_probability'] = probability
        play['natural_frequency'] = int(probability * 10 + 0.5)
        play['fair_odds'] = (
            american_from_decimal(1.0 / probability)
            if 0.0 < probability < 1.0 else None
        )

        try:
            line = float(play.get('line'))
        except (TypeError, ValueError):
            line = 0.0
        try:
            spread = max(abs(float(play.get('std_dev') or 0.0)) * 2.0, 1.0)
        except (TypeError, ValueError):
            spread = 1.0

        stat_col = _STAT_COL.get(play.get('prop_type', ''))
        recent_form = []
        # Rows were collected newest-first; the strip reads oldest to newest.
        for log in reversed(logs_by_player.get(play.get('player', ''), [])):
            value = getattr(log, stat_col, None) if stat_col else None
            if value is None:
                continue
            value = float(value)
            margin = value - line
            result = 'push' if margin == 0 else ('over' if margin > 0 else 'under')
            won = result == side
            recent_form.append({
                'date': log.game_date.strftime('%b %d') if log.game_date else '',
                'value': value,
                'margin_normalized': round(max(-1.0, min(1.0, margin / spread)), 3),
                'tone': 'push' if result == 'push' else ('win' if won else 'loss'),
                'result': result,
            })
        play['recent_form'] = recent_form

    return view_models


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
            logger.warning("Skipping analysis row due to unexpected error", exc_info=True)
            continue

    return resolved


def _recent_logs_by_player(scores: list[dict], limit: int = 20) -> dict[str, list]:
    player_names = {score.get('player') for score in scores if score.get('player')}
    logs_by_player: dict[str, list] = defaultdict(list)
    if not player_names:
        return logs_by_player
    logs = (
        PlayerGameLog.query
        .filter(PlayerGameLog.player_name.in_(list(player_names)))
        .order_by(PlayerGameLog.game_date.desc())
        .all()
    )
    for log in logs:
        if len(logs_by_player[log.player_name]) < limit:
            logs_by_player[log.player_name].append(log)
    return logs_by_player


def _latest_team_abbrs(logs_by_player: dict[str, list]) -> dict[str, str]:
    team_abbrs = {}
    for player_name, logs in logs_by_player.items():
        latest_with_team = next((log for log in logs if log.team_abbr), None)
        if latest_with_team:
            team_abbrs[player_name] = latest_with_team.team_abbr.upper()
    return team_abbrs


def _opponent_abbrs(scores: list[dict], game_lookup: dict) -> set[str]:
    opponents = set()
    for score in scores:
        game = game_lookup.get(score.get('game_id'), {})
        player_team = (score.get('player_team_abbr') or '').upper()
        home_abbr = ((game.get('home') or {}).get('abbr') or '').upper()
        away_abbr = ((game.get('away') or {}).get('abbr') or '').upper()
        opponent = away_abbr if player_team == home_abbr else home_abbr
        if opponent:
            opponents.add(opponent)
    return opponents


def _latest_defense_snapshots(opponent_abbrs: set[str]) -> dict:
    if not opponent_abbrs:
        return {}
    rows = (
        TeamDefenseSnapshot.query
        .filter(TeamDefenseSnapshot.team_abbr.in_(list(opponent_abbrs)))
        .order_by(TeamDefenseSnapshot.fetched_at.desc())
        .all()
    )
    snapshots = {}
    for row in rows:
        snapshots.setdefault(row.team_abbr, row)
    return snapshots


def _stat_indicator(score: dict) -> str:
    indicator = {
        'strong': 'strong',
        'moderate': 'value',
        'slight': 'slight',
    }.get(score.get('confidence_tier'), 'avoid')
    if (score.get('win_probability') or 0.5) < 0.40:
        return 'avoid'
    return indicator


def _enrich_stat_scores(scores: list[dict], game_lookup: dict) -> list[dict]:
    enriched_scores = [dict(score) for score in scores]
    logs_by_player = _recent_logs_by_player(enriched_scores)
    team_abbrs = _latest_team_abbrs(logs_by_player)
    for score in enriched_scores:
        if not score.get('player_team_abbr'):
            score['player_team_abbr'] = team_abbrs.get(score.get('player', ''), '')
    defense_snapshots = _latest_defense_snapshots(
        _opponent_abbrs(enriched_scores, game_lookup)
    )
    for score in enriched_scores:
        try:
            line = float(score.get('line') or 0)
        except (TypeError, ValueError):
            line = 0.0
        stat_column = _STAT_COL.get(score.get('prop_type', ''))
        score['hit_rates'] = _hit_rates_from_logs(
            logs_by_player.get(score.get('player', ''), []), stat_column, line
        )
        score['game_ctx'] = build_stat_context(
            score, game_lookup, defense_snapshots
        )
        score['indicator'] = _stat_indicator(score)
    return enriched_scores


def _group_stat_scores(scores: list[dict], games: list[dict]) -> list[dict]:
    grouped = {
        game.get('espn_id'): {'meta': game, 'home': [], 'away': []}
        for game in games
    }
    for score in scores:
        game = grouped.get(score.get('game_id'))
        if game is None:
            continue
        player_team = (score.get('player_team_abbr') or '').upper()
        home_abbr = ((game['meta'].get('home') or {}).get('abbr') or '').upper()
        game['home' if player_team == home_abbr else 'away'].append(score)
    for game in grouped.values():
        for bucket in ('home', 'away'):
            game[bucket].sort(
                key=lambda score: POSITION_ORDER.get(
                    (score.get('breakdown') or {}).get('player_position', ''), 99
                )
            )
    return [game for game in grouped.values() if game['home'] or game['away']]


def _filter_stat_matchups(
    matchups: list[dict], stat_filter: str, search_query: str
) -> None:
    if stat_filter == 'all' and not search_query:
        return
    for matchup in matchups:
        for bucket in ('home', 'away'):
            matchup[bucket] = [
                score for score in matchup[bucket]
                if (stat_filter == 'all' or score.get('prop_type') == stat_filter)
                and (
                    not search_query
                    or search_query in (score.get('player') or '').lower()
                )
            ]


def _stat_matchup_counts(matchups: list[dict]) -> dict[str, int]:
    scores = [
        score
        for matchup in matchups
        for score in matchup['home'] + matchup['away']
    ]
    return {
        'total': len(scores),
        'strong_ct': sum(score.get('indicator') == 'strong' for score in scores),
        'value_ct': sum(score.get('indicator') == 'value' for score in scores),
        'avoid_ct': sum(score.get('indicator') == 'avoid' for score in scores),
    }


# ── Routes ────────────────────────────────────────────────────────────────

@login_required
@limiter.limit("6 per minute")
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
        value_plays = _analysis_play_view_models(eligible_plays[:50])
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
    from app.services.nba_service import get_todays_games as _get_todays_games

    try:
        scores = get_todays_scores()
    except Exception as exc:
        logger.error("Stat analysis engine error: %s", exc)
        scores = []

    games_today = _get_todays_games()
    game_lookup = {game.get('espn_id'): game for game in games_today}
    scores = _enrich_stat_scores(scores, game_lookup)
    matchups = _group_stat_scores(scores, games_today)

    stat_filter = request.args.get('stat', 'all')
    search_q = request.args.get('q', '').strip().lower()
    _filter_stat_matchups(matchups, stat_filter, search_q)
    counts = _stat_matchup_counts(matchups)

    return render_template('bets/nba_stat_analysis.html',
                           matchups=matchups,
                           stat_filter=stat_filter,
                           search_q=search_q,
                           **counts)
