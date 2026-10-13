"""Pure validation for bounded NBA progress and bet placement requests."""

import math
import json
import re
import uuid

from app.enums import BetType

MAX_PROGRESS_ITEMS = 100
MAX_PROGRESS_GAMES = 15
MAX_PLACEMENT_LEGS = 100


class RequestValidationError(ValueError):
    """A malformed request that must be rejected before doing work."""


def finite_number(value, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise RequestValidationError(f'{field} must be a finite number')
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise RequestValidationError(f'{field} must be a finite number') from exc
    if not math.isfinite(number):
        raise RequestValidationError(f'{field} must be a finite number')
    return number


def _identifier(value, field: str) -> str:
    # Numeric IDs are accepted for legacy callers, but never booleans/floats.
    if type(value) is int:
        value = str(value)
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', value):
        raise RequestValidationError(f'Invalid {field} format')
    return value


def _text(value, field: str, maximum: int, *, required: bool = True) -> str:
    if value is None and not required:
        return ''
    if not isinstance(value, str) or len(value) > maximum:
        raise RequestValidationError(f'{field} must be a string of at most {maximum} characters')
    value = value.strip()
    if required and not value:
        raise RequestValidationError(f'{field} is required')
    return value


def validate_progress_item(item) -> dict:
    if not isinstance(item, dict):
        raise RequestValidationError('Each progress item must be an object')
    card_id = item.get('card_id')
    if type(card_id) is int:
        card_id = str(card_id)
    # Card IDs may be legacy URLs, so only their type and length are bounded.
    _text(card_id, 'card_id', 512)
    bet_type = _text(item.get('bet_type'), 'bet_type', 20, required=False).lower()
    if bet_type not in ('', BetType.OVER.value, BetType.UNDER.value):
        raise RequestValidationError('bet_type must be over or under')
    line = item.get('line')
    return {
        'card_id': card_id,
        'espn_id': _identifier(item.get('espn_id'), 'espn_id'),
        'player': _text(item.get('player'), 'player', 100),
        'prop_type': _text(item.get('prop_type'), 'prop_type', 40),
        'line': 0.0 if line is None or line == '' else finite_number(line, 'line'),
        'bet_type': bet_type,
    }


def validate_progress_batch(body) -> list[dict]:
    if not isinstance(body, list) or not body:
        raise RequestValidationError('Expected non-empty JSON array')
    if len(body) > MAX_PROGRESS_ITEMS:
        raise RequestValidationError(f'At most {MAX_PROGRESS_ITEMS} progress items are allowed')
    items = [validate_progress_item(item) for item in body]
    if len({item['card_id'] for item in items}) != len(items):
        raise RequestValidationError('card_id must be unique within the batch')
    if len({item['espn_id'] for item in items}) > MAX_PROGRESS_GAMES:
        raise RequestValidationError(f'At most {MAX_PROGRESS_GAMES} unique games are allowed')
    return items


def _optional_number(value, field: str, *, integer: bool = False) -> None:
    if value is None or value == '':
        return
    number = finite_number(value, field)
    if integer and (not number.is_integer() or not -(2 ** 31) <= number < 2 ** 31):
        raise RequestValidationError(f'{field} must be a 32-bit integer')


def validate_placement_request(data) -> None:
    if not isinstance(data, dict) or not data:
        raise RequestValidationError('Invalid request: expected a JSON object')
    try:
        json.dumps(data, allow_nan=False)
    except (ValueError, TypeError):
        raise RequestValidationError('Request must contain finite JSON values') from None
    if 'request_id' in data:
        value = data['request_id']
        try:
            if not isinstance(value, str) or str(uuid.UUID(value)) != value:
                raise ValueError()
        except (ValueError, AttributeError):
            raise RequestValidationError('request_id must be a canonical UUID') from None
    legs = data.get('legs')
    if not isinstance(legs, list) or not legs:
        raise RequestValidationError('No selections provided: legs must be a non-empty array')
    if len(legs) > MAX_PLACEMENT_LEGS:
        raise RequestValidationError(f'At most {MAX_PLACEMENT_LEGS} selections are allowed')
    if 'is_parlay' in data and not isinstance(data['is_parlay'], bool):
        raise RequestValidationError('is_parlay must be a boolean')
    for field in ('stake', 'units', 'bonus_multiplier'):
        _optional_number(data.get(field), field)
    round_robin = data.get('round_robin')
    if round_robin is not None:
        if not isinstance(round_robin, dict):
            raise RequestValidationError('round_robin must be an object')
        _optional_number(round_robin.get('size'), 'round_robin.size', integer=True)
    for index, leg in enumerate(legs, start=1):
        if not isinstance(leg, dict):
            raise RequestValidationError(f'Leg {index}: must be an object')
        if 'manual_entry' in leg and not isinstance(leg['manual_entry'], bool):
            raise RequestValidationError(f'Leg {index}: manual_entry must be a boolean')
        if 'recorded_odds_edited' in leg and not isinstance(leg['recorded_odds_edited'], bool):
            raise RequestValidationError(f'Leg {index}: recorded_odds_edited must be a boolean')
        for field, maximum in (('quote_token', 8192), ('quote_source', 40),
                               ('quote_observed_at', 80), ('quote_book', 80),
                               ('selection_key', 150)):
            _text(leg.get(field), f'Leg {index}: {field}', maximum, required=False)
        for field in ('quote_line', 'quote_american_odds'):
            _optional_number(leg.get(field), f'Leg {index}: {field}', integer=field.endswith('odds'))
        for field in ('team_a', 'team_b'):
            _text(leg.get(field), f'Leg {index}: {field}', 4096)
        for field in ('player_name', 'prop_type', 'picked_team'):
            _text(leg.get(field), f'Leg {index}: {field}', 4096, required=False)
        bet_type = leg.get('bet_type', BetType.OVER.value)
        if not isinstance(bet_type, str) or bet_type not in {kind.value for kind in BetType}:
            raise RequestValidationError(f'Leg {index}: invalid bet_type')
        game_id = leg.get('game_id')
        if game_id is not None and game_id != '':
            _identifier(game_id, f'Leg {index}: game_id')
        for field in ('prop_line', 'over_under_line', 'american_odds'):
            _optional_number(leg.get(field), f'Leg {index}: {field}', integer=field == 'american_odds')
    if any(leg.get('quote_token') for leg in legs) and not data.get('request_id'):
        raise RequestValidationError('request_id is required for a guided quote record')
