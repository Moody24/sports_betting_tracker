"""Signed user-bound quote evidence, checked against durable latest observations.

Tokens identify a served observation, not a guaranteed execution price. Saving
never fetches a provider. Expired observations are pruned when quotes are served.
"""

import hashlib
import json
import math
from datetime import datetime, timedelta, timezone

from flask import current_app
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy import case, or_, update
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import GameSnapshot, GuidedQuote
from app.services.nba_service import is_fresh_prop_observation
from app.utils.time_helpers import ET


class GuidedQuoteError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _serializer():
    return URLSafeSerializer(current_app.config['SECRET_KEY'], salt='guided-prop-quote-v1')


def _json(payload):
    return json.dumps(payload, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _quote_payload(prop, side):
    book = prop.get(f'best_{side}_book')
    quoted = (prop.get('books') or {}).get(book, {})
    line, odds = _number(prop.get('line')), _number(prop.get(f'{side}_odds'))
    observed = prop.get('quote_observed_at')
    if (not book or line is None or odds is None or not odds.is_integer()
            or not -5000 <= odds <= 5000 or abs(odds) < 100
            or line != _number(quoted.get('line')) or odds != _number(quoted.get(f'{side}_odds'))
            or not is_fresh_prop_observation(observed)
            or not is_fresh_prop_observation(quoted.get('last_update'))):
        return None
    if prop.get('game_status') not in {'scheduled', 'in_progress'} or not prop.get('odds_event_id'):
        return None
    return {
        'source': 'odds_api', 'event_id': str(prop['odds_event_id']),
        'selection_key': prop['selection_key'], 'game_id': str(prop.get('game_id') or ''),
        'player': prop['player'], 'market': prop['market'], 'side': side,
        'book': book, 'line': line, 'odds': int(odds), 'observed_at': observed,
        'team_a': prop['team_a'], 'team_b': prop['team_b'],
        'match_date': prop['match_date'], 'start_time': prop['start_time'],
    }


def _identity(payload):
    fields = ('source', 'event_id', 'player', 'market', 'side', 'book')
    return hashlib.sha256(_json({key: payload[key] for key in fields}).encode()).hexdigest()


def _store_quote(payload):
    key = _identity(payload)
    observed = datetime.fromisoformat(payload['observed_at'].replace('Z', '+00:00')).astimezone(timezone.utc)
    row = db.session.get(GuidedQuote, key)
    if row is None:
        try:
            with db.session.begin_nested():
                row = GuidedQuote(key=key, event_id=payload['event_id'], payload_json=_json(payload),
                                  observed_at=observed, available=True)
                db.session.add(row)
                db.session.flush()
            return key
        except IntegrityError:
            pass
    # Active rows may replay the same observation. An inactive row's observed_at
    # is a withdrawal ordering watermark: reopening requires strictly newer
    # source evidence. Actual source time remains unchanged in payload_json.
    result = db.session.execute(update(GuidedQuote).where(
        GuidedQuote.key == key,
        or_(
            (GuidedQuote.available.is_(True)) & (GuidedQuote.observed_at <= observed),
            (GuidedQuote.available.is_(False)) & (GuidedQuote.observed_at < observed),
        ),
    ).values(payload_json=_json(payload), observed_at=observed, available=True)
        .execution_options(synchronize_session=False))
    return key if result.rowcount else None


def attach_quote_tokens(props, user_id, *, unavailable_events=()):
    """Add side tokens to verified current rows; persist only latest observations."""
    now = datetime.now(timezone.utc)
    GuidedQuote.query.filter(GuidedQuote.observed_at < now - timedelta(minutes=10)).delete(
        synchronize_session=False,
    )
    prepared = [(prop, [(side, payload) for side in ('over', 'under')
                        if (payload := _quote_payload(prop, side)) is not None]) for prop in props]
    observed_keys = {_identity(payload) for _, sides in prepared for _, payload in sides}
    for event_id in unavailable_events:
        observations = [payload['observed_at'] for _, sides in prepared
                        for _, payload in sides if payload['event_id'] == event_id]
        missing = GuidedQuote.query.filter(GuidedQuote.event_id == event_id)
        if observed_keys:
            missing = missing.filter(GuidedQuote.key.notin_(observed_keys))
        if observations:
            newest = max(datetime.fromisoformat(value.replace('Z', '+00:00')) for value in observations)
            missing = missing.filter(GuidedQuote.observed_at <= newest)
        # A missing quote is evidence observed at receipt time, not at the last
        # positive quote's source timestamp. Keep this durable fence so delayed
        # responses cannot resurrect a withdrawn market. This intentionally
        # waits for a new source update when a provider reuses old timestamps.
        missing.update({
            'available': False,
            'observed_at': case((GuidedQuote.observed_at > now, GuidedQuote.observed_at), else_=now),
        }, synchronize_session=False)
    for prop, sides in prepared:
        prop['quote_tokens'], prop['quote_books'] = {}, {}
        for side, payload in sides:
            key = _store_quote(payload)
            if key is None:
                continue
            prop['quote_tokens'][side] = _serializer().dumps({'user_id': user_id, 'key': key, 'quote': payload})
            prop['quote_books'][side] = payload['book']
    db.session.commit()
    return props


def validate_guided_legs(legs, user_id, *, require_evidence=False):
    """Reject altered/expired guided records; manual values remain untouched."""
    for leg in legs:
        token = leg.get('quote_token')
        if leg.get('manual_entry') is True:
            if token:
                raise GuidedQuoteError('quote_changed', 'Remove live quote evidence to record a manual price.')
            continue
        if not token:
            if require_evidence or any(leg.get(key) for key in ('quote_source', 'quote_observed_at')):
                raise GuidedQuoteError('quote_required', 'Choose an available current prop or record this bet manually.')
            continue  # Legacy unsigned records are labeled manual at persistence.
        try:
            evidence = _serializer().loads(token)
        except (BadSignature, TypeError, ValueError):
            raise GuidedQuoteError('quote_invalid', 'Quote evidence is invalid. Refresh the available props.') from None
        if not isinstance(evidence, dict) or evidence.get('user_id') != user_id:
            raise GuidedQuoteError('quote_invalid', 'Quote evidence is invalid for this account.')
        quote = evidence.get('quote')
        if not isinstance(quote, dict) or evidence.get('key') != _identity(quote):
            raise GuidedQuoteError('quote_invalid', 'Quote evidence is invalid. Refresh the available props.')
        if not is_fresh_prop_observation(quote.get('observed_at')):
            raise GuidedQuoteError('quote_expired', 'This quote expired. Refresh props before recording it.')
        row = db.session.get(GuidedQuote, evidence['key'], populate_existing=True)
        if row is None or not row.available:
            raise GuidedQuoteError('quote_unavailable', 'This prop is no longer available. Refresh props or record it manually.')
        latest = json.loads(row.payload_json)
        # A newer identical price remains valid during the original token TTL.
        identity_fields = tuple(key for key in quote if key != 'observed_at')
        if any(latest.get(key) != quote.get(key) for key in identity_fields):
            raise GuidedQuoteError('quote_changed', 'The available quote changed. Confirm the current price or keep a manual record.')
        expected = {
            'player_name': quote['player'], 'prop_type': quote['market'], 'bet_type': quote['side'],
            'game_id': quote['game_id'], 'selection_key': quote['selection_key'],
            'team_a': quote['team_a'], 'team_b': quote['team_b'], 'match_date': quote['match_date'],
            'quote_source': quote['source'], 'quote_observed_at': quote['observed_at'],
            'quote_book': quote['book'],
        }
        if any(str(leg.get(key) or '') != str(value) for key, value in expected.items()) or any(
            _number(leg.get(key)) != value for key, value in {
                'prop_line': quote['line'], 'american_odds': quote['odds'],
                'quote_line': quote['line'], 'quote_american_odds': quote['odds'],
            }.items()
        ) or leg.get('recorded_odds_edited') is True:
            raise GuidedQuoteError('quote_changed', 'The recorded selection differs from its quote. Confirm it as a manual record.')
        today = datetime.now(ET).date()
        if quote['match_date'] not in {today.isoformat(), (today + timedelta(days=1)).isoformat()}:
            raise GuidedQuoteError('quote_expired', 'The selected game is outside the current recording window.')
        if quote['game_id'] and GameSnapshot.query.filter_by(
            espn_id=quote['game_id'], is_final=True,
        ).first():
            raise GuidedQuoteError('quote_unavailable', 'This game has finished. Record a historical bet manually.')
