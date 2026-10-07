"""One scheduled free-tier Games fetch shared through durable snapshots.

Read helpers never contact providers. BALLDONTLIE IDs remain separate from
ESPN identifiers, and fetch receipt timestamps do not claim provider latency.
"""

import json
import math
import os
import threading
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import requests
from flask import current_app

from app import db
from app.models import BasketballFeedEvent, BasketballFeedState
from app.utils.time_helpers import ET

FEED_KEY = 'nba_games'
GAMES_URL = 'https://api.balldontlie.io/v1/games'
POLL_INTERVAL_SECONDS = 30
FRESH_SECONDS = 75
STALE_SECONDS = 900
MAX_EVENTS = 100
MAX_RESPONSE_BYTES = 1024 * 1024
_collector_lock = threading.Lock()
_LIFECYCLES = {'scheduled', 'in_progress', 'final', 'postponed', 'canceled',
               'delayed', 'suspended', 'abandoned', 'unknown'}


def _now(value=None):
    return (value or datetime.now(timezone.utc)).astimezone(timezone.utc)


def _aware(value):
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _iso(value):
    return _aware(value).isoformat() if value is not None else None


def _api_key():
    return str(current_app.config.get('BALLDONTLIE_API_KEY',
                                     os.getenv('BALLDONTLIE_API_KEY', '')) or '').strip()


def _payload(row):
    return json.loads(row.payload_json) if row and row.payload_json else {}


def _snapshot(row, now):
    today = now.astimezone(ET).date().isoformat()
    stored = _payload(row)
    last_success = _aware(row.last_success_at) if row else None
    age = (now - last_success).total_seconds() if last_success else None
    source_day = stored.get('_source_match_date', stored.get('match_date'))
    valid_day = (source_day == today and last_success is not None
                 and last_success.astimezone(ET).date().isoformat() == today)
    enabled = bool(_api_key())
    state = 'disabled' if not enabled else 'unavailable'
    games = []
    if enabled and valid_day and age is not None and 0 <= age <= STALE_SECONDS:
        games = stored.get('games', [])
        state = 'current' if age <= FRESH_SECONDS and not row.error_code else 'stale'
    return {
        'revision': row.revision if row else 0,
        'source': 'balldontlie', 'state': state, 'games': games,
        'match_date': today,
        'last_success_at': _iso(last_success),
        'last_attempt_at': _iso(row.last_attempt_at) if row else None,
        'error_code': row.error_code if row and enabled else None,
        'provider_delay': 'Provider delay not guaranteed',
        'poll_interval_seconds': POLL_INTERVAL_SECONDS,
    }


def get_feed_snapshot(*, now=None):
    """Return latest DB state, with truthful age/day overlays and no HTTP."""
    row = db.session.get(BasketballFeedState, FEED_KEY, populate_existing=True)
    return _snapshot(row, _now(now))


def read_feed_events(after_revision, *, now=None):
    """Return bounded ascending replay or a reset when a cursor is unusable."""
    snapshot = get_feed_snapshot(now=now)
    result = {'reset': True, 'events': [], 'snapshot': snapshot}
    if isinstance(after_revision, bool):
        return result
    try:
        raw = str(after_revision)
        if not raw.isascii() or not raw.isdigit() or len(raw) > 18:
            return result
        revision = int(raw)
    except (ValueError, TypeError):
        return result
    if revision > snapshot['revision']:
        return result
    oldest = (BasketballFeedEvent.query.filter_by(key=FEED_KEY)
              .order_by(BasketballFeedEvent.id).first())
    if revision < snapshot['revision'] and (not oldest or revision < oldest.id - 1):
        return result
    events = (BasketballFeedEvent.query.filter_by(key=FEED_KEY)
              .filter(BasketballFeedEvent.id > revision,
                      BasketballFeedEvent.id <= snapshot['revision'])
              .order_by(BasketballFeedEvent.id).limit(MAX_EVENTS).all())
    replay = [
        {'revision': event.id, 'payload': json.loads(event.payload_json)} for event in events
    ]
    if replay and (any(event['payload']['match_date'] != snapshot['match_date'] for event in replay)
                   or replay[-1]['payload']['state'] != snapshot['state']):
        return result
    result.update(reset=False, events=replay)
    return result


class _FeedError(Exception):
    def __init__(self, code, retry_after=None):
        super().__init__(code)
        self.code = code
        self.retry_after = retry_after


def _retry_after(value, now):
    if not isinstance(value, str):
        return None
    try:
        delay = float(value)
    except ValueError:
        try:
            delay = (_aware(parsedate_to_datetime(value)) - now).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return None
    return delay if math.isfinite(delay) and 0 <= delay <= 7 * 86400 else None


def _text(value, maximum):
    if not isinstance(value, str) or len(value) > maximum:
        raise _FeedError('invalid_payload')
    return value.strip()


def _number(value):
    if value is None:
        return None
    if type(value) is not int or not 0 <= value <= 1000:
        raise _FeedError('invalid_payload')
    return value


def _normalize_game(row):
    if not isinstance(row, dict) or type(row.get('id')) is not int or row['id'] <= 0:
        raise _FeedError('invalid_payload')
    try:
        start = datetime.fromisoformat(row['datetime'].replace('Z', '+00:00')) if row.get('datetime') else None
        if start and start.tzinfo is None:
            raise ValueError('Ambiguous timestamp')
        match_date = start.astimezone(ET).date() if start else date.fromisoformat(row['date'])
        teams = {}
        for side, prefix in (('home', 'home'), ('away', 'visitor')):
            team = row[f'{prefix}_team']
            name = _text(team['full_name'], 100)
            abbr = _text(team['abbreviation'], 10)
            if not name or not abbr:
                raise _FeedError('invalid_payload')
            teams[side] = {'name': name, 'abbr': abbr,
                           'score': _number(row.get(f'{prefix}_team_score'))}
        status = row.get('status_state')
        if status not in _LIFECYCLES:
            raw_status = _text(row.get('status', ''), 100).lower()
            status = ('postponed' if row.get('postponed') is True else
                      'final' if raw_status == 'final' else
                      'in_progress' if 'qtr' in raw_status or raw_status == 'halftime' else
                      'scheduled' if row.get('period') == 0 and start else 'unknown')
        return {
            'source_event_id': f"balldontlie:{row['id']}", **teams,
            'status': status, 'period': _number(row.get('period')),
            'clock': _text(row.get('time') or '', 40) or None,
            'start_time': _iso(start), 'match_date': match_date.isoformat(),
        }
    except (KeyError, TypeError, ValueError, AttributeError):
        raise _FeedError('invalid_payload') from None


def _fetch_games(key, today, now):
    response = None
    try:
        response = requests.get(
            GAMES_URL, headers={'Authorization': key, 'Accept': 'application/json'},
            params={'dates[]': today, 'per_page': 100}, timeout=5,
            allow_redirects=False, stream=True,
        )
        if response.status_code != 200:
            raise _FeedError(f'http_{response.status_code}',
                             _retry_after(response.headers.get('Retry-After'), now))
        chunks, size = [], 0
        for chunk in response.iter_content(chunk_size=65536):
            size += len(chunk)
            if size > MAX_RESPONSE_BYTES:
                raise _FeedError('response_too_large')
            chunks.append(chunk)
        payload = json.loads(b''.join(chunks))
        if not isinstance(payload, dict) or not isinstance(payload.get('data'), list):
            raise _FeedError('invalid_payload')
        meta = payload.get('meta')
        if not isinstance(meta, dict):
            raise _FeedError('invalid_payload')
        if meta.get('next_cursor') is not None or len(payload['data']) > 100:
            raise _FeedError('incomplete_page')
        games = [_normalize_game(row) for row in payload['data']]
        if len({game['source_event_id'] for game in games}) != len(games):
            raise _FeedError('invalid_payload')
        return sorted((game for game in games if game['match_date'] == today),
                      key=lambda game: (game['start_time'] or '', game['source_event_id']))
    except requests.Timeout:
        raise _FeedError('timeout') from None
    except requests.RequestException:
        raise _FeedError('transport_error') from None
    except (ValueError, TypeError):
        raise _FeedError('invalid_payload') from None
    finally:
        if response is not None:
            response.close()


def _publish(row, now, previous):
    stored = _payload(row)
    source_day = stored.get('_source_match_date', stored.get('match_date'))
    snapshot = _snapshot(row, now)
    content_keys = ('source', 'state', 'games', 'match_date', 'error_code')
    if any(snapshot.get(key) != previous.get(key) for key in content_keys):
        event = BasketballFeedEvent(key=FEED_KEY, payload_json='{}', created_at=now)
        db.session.add(event)
        db.session.flush()
        row.revision = event.id
        row.observed_at = now
        snapshot['revision'] = event.id
        event.payload_json = json.dumps(snapshot, separators=(',', ':'), sort_keys=True)
        old = (BasketballFeedEvent.query.filter_by(key=FEED_KEY)
               .order_by(BasketballFeedEvent.id.desc()).offset(MAX_EVENTS).first())
        if old:
            BasketballFeedEvent.query.filter(
                BasketballFeedEvent.key == FEED_KEY,
                BasketballFeedEvent.id <= old.id,
            ).delete(synchronize_session=False)
    # The public view uses today's date, but a request started before ET
    # midnight may have fetched yesterday. Preserve that provenance on reread.
    row.payload_json = json.dumps({**snapshot, '_source_match_date': source_day},
                                  separators=(',', ':'), sort_keys=True)
    db.session.commit()
    return snapshot


def collect_basketball_feed(*, now=None):
    """One scheduler-owned attempt; persisted cooldown caps requests at 2/min."""
    use_clock = now is None
    now = _now(now)
    if not _collector_lock.acquire(blocking=False):
        return get_feed_snapshot(now=now)
    try:
        row = db.session.get(BasketballFeedState, FEED_KEY, populate_existing=True)
        if row is None:
            row = BasketballFeedState(key=FEED_KEY, revision=0, payload_json='{}',
                                      source='balldontlie', failure_count=0)
            db.session.add(row)
        previous = _payload(row)
        key = _api_key()
        if not key:
            row.error_code = None
            row.last_success_at = None
            return _publish(row, now, previous)
        retry_at, last_attempt = _aware(row.retry_at), _aware(row.last_attempt_at)
        if ((retry_at and now < retry_at) or
                (last_attempt and (now - last_attempt).total_seconds() < POLL_INTERVAL_SECONDS)):
            return _publish(row, now, previous)
        row.last_attempt_at = now
        row.retry_at = now + timedelta(seconds=POLL_INTERVAL_SECONDS)
        db.session.commit()  # Persist cooldown before I/O; process restart cannot bypass it.
        today = now.astimezone(ET).date().isoformat()
        try:
            games = _fetch_games(key, today, now)
        except _FeedError as error:
            now = _now() if use_clock else now
            row.error_code = error.code
            row.failure_count = min((row.failure_count or 0) + 1, 10)
            delay = min(300, POLL_INTERVAL_SECONDS * 2 ** (row.failure_count - 1))
            if error.code in ('http_401', 'http_403'):
                delay = 300
            row.retry_at = now + timedelta(seconds=max(delay, error.retry_after or 0))
        else:
            now = _now() if use_clock else now
            row.last_success_at = now
            row.error_code = None
            row.failure_count = 0
            row.payload_json = json.dumps({'match_date': today, 'games': games})
        return _publish(row, now, previous)
    except Exception:
        db.session.rollback()
        raise
    finally:
        _collector_lock.release()
