"""Presentation of provider availability without making provider requests."""

from datetime import datetime
import os

from flask import current_app, has_app_context

from app.services.nba_service import get_scoreboard_status
from app.services.provider_access import provider_access_status
from app.utils.time_helpers import ET


def _display_time(value: str | None) -> str | None:
    if not value:
        return None
    try:
        timestamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if timestamp.tzinfo is None:
            return None
        return timestamp.astimezone(ET).strftime('%b %d, %I:%M %p ET')
    except (TypeError, ValueError):
        return None


def data_status_context() -> dict:
    access = provider_access_status()
    scoreboard = dict(get_scoreboard_status())
    scoreboard['label'] = 'ESPN scores'
    scoreboard['last_success_display'] = _display_time(
        scoreboard.get('last_success_at')
    )
    if not access['espn']['enabled']:
        scoreboard.update(state='unavailable', age_label='Source approval required')
    elif scoreboard.get('state') == 'fresh':
        scoreboard['age_label'] = 'Recent provider fetch'
    elif scoreboard.get('state') == 'stale':
        scoreboard['age_label'] = 'Last known data · refresh unavailable'
    else:
        scoreboard['age_label'] = 'No verified current data'

    feed_key = (current_app.config.get('BALLDONTLIE_API_KEY', os.getenv('BALLDONTLIE_API_KEY', ''))
                if has_app_context() else '')
    if feed_key:
        from app.services.basketball_feed import get_feed_snapshot
        try:
            feed = get_feed_snapshot()
        except Exception:
            feed = {'state': 'unavailable'}
        state = 'fresh' if feed['state'] == 'current' else feed['state']
        scoreboard = {
            'label': 'NBA scores', 'state': state,
            'age_label': {
                'fresh': 'BALLDONTLIE fetched · provider delay not guaranteed',
                'stale': 'BALLDONTLIE last known data · refresh unavailable',
            }.get(state, 'BALLDONTLIE · no verified current data'),
            'last_success_display': _display_time(feed.get('last_success_at')),
        }

    odds_enabled = access['odds_api']['enabled']
    return {
        'scoreboard': scoreboard,
        'odds': {
            'label': 'Market odds',
            'state': 'configured' if odds_enabled else 'unavailable',
            'age_label': (
                'Provider configured · inspect each quote time' if odds_enabled
                else 'Not connected · manual recording available'
            ),
        },
    }
