"""Normalize verified scheduled/live events without joining provider namespaces."""

from datetime import datetime, timedelta

from app.services.nba_service import game_selection_key, game_start_date_et
from app.utils.time_helpers import ET

_ACTIVE = {
    'STATUS_SCHEDULED': 'scheduled', 'STATUS_PRE': 'scheduled',
    'STATUS_IN_PROGRESS': 'in_progress', 'STATUS_HALFTIME': 'in_progress',
    'STATUS_END_PERIOD': 'in_progress',
}


def game_picker_options(today_games, tomorrow_games, feed, *, now=None):
    now = now or datetime.now(ET)
    today = now.astimezone(ET).date()
    dates = {today.isoformat(), (today + timedelta(days=1)).isoformat()}
    results = []
    for game in [*today_games, *tomorrow_games]:
        status = _ACTIVE.get(game.get('status'))
        match_date = game_start_date_et(game.get('start_time', ''))
        selection_key = game_selection_key(game)
        if not status or match_date not in dates or not selection_key:
            continue
        source = 'espn' if game.get('espn_id') else 'odds_api'
        suffix = ' (Tomorrow)' if match_date != today.isoformat() else ''
        results.append({
            'label': f"{game['away']['name']} @ {game['home']['name']}{suffix}",
            'team_a': game['away']['name'], 'team_b': game['home']['name'],
            'match_date': match_date, 'start_time': game['start_time'],
            'status': status, 'source': source,
            'game_id': game.get('espn_id', ''), 'selection_key': selection_key,
            'odds_event_id': game.get('odds_event_id', ''),
            'over_under_line': game.get('over_under_line'),
            'moneyline_away': game.get('moneyline_away'),
            'moneyline_home': game.get('moneyline_home'),
        })
    if feed.get('state') == 'current' and feed.get('match_date') == today.isoformat():
        for game in feed.get('games', []):
            if game['status'] not in {'scheduled', 'in_progress'} or game['match_date'] not in dates:
                continue
            results.append({
                'label': f"{game['away']['name']} @ {game['home']['name']} · Scores only",
                'team_a': game['away']['name'], 'team_b': game['home']['name'],
                'match_date': game['match_date'], 'start_time': game['start_time'],
                'status': game['status'], 'source': 'balldontlie',
                'source_observed_at': feed.get('last_success_at'),
                'game_id': '', 'selection_key': game['source_event_id'],
                'odds_event_id': '', 'over_under_line': None,
                'moneyline_away': None, 'moneyline_home': None,
                'market_state': 'unavailable',
            })
    return list({game['selection_key']: game for game in results}.values())
