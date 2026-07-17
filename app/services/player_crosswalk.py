"""Live player-name -> ESPN athlete-id resolver.

ScenarioSplit rows are keyed by ESPN ids (the historical store's namespace)
but live scoring only has display names (odds API / NBA namespace). The
bridge is the name: ~580 split players, resolved via aggressive
normalization against ScenarioSplit's own (player_id, player_name) pairs.
Collisions are dropped, never guessed — a prop either matches the right
player or shows no scenario signal.
"""

import logging
import re
import unicodedata
from functools import lru_cache

from flask import current_app

from app.models import ScenarioSplit

logger = logging.getLogger(__name__)

_SUFFIXES = {'jr', 'sr', 'ii', 'iii', 'iv', 'v'}

# Normalized-name -> ESPN id, for spellings normalization can't bridge.
OVERRIDES: dict[str, str] = {}


def normalize_name(name: str) -> str:
    ascii_name = unicodedata.normalize('NFKD', name or '').encode(
        'ascii', 'ignore').decode('ascii')
    cleaned = re.sub(r"[^a-z ]", '', ascii_name.lower().replace('-', ' '))
    tokens = [token for token in cleaned.split() if token not in _SUFFIXES]
    return ' '.join(tokens)


@lru_cache(maxsize=None)
def _name_map(_app_identity: int) -> dict:
    pairs = (ScenarioSplit.query
             .with_entities(ScenarioSplit.player_id,
                            ScenarioSplit.player_name)
             .distinct().all())
    mapping: dict[str, str] = {}
    collided: set[str] = set()
    for player_id, player_name in pairs:
        key = normalize_name(player_name)
        if not key:
            continue
        if key in mapping and mapping[key] != str(player_id):
            collided.add(key)
            continue
        mapping[key] = str(player_id)
    for key in collided:
        mapping.pop(key, None)
        logger.warning("player_crosswalk: name collision dropped: %r", key)
    return mapping


def resolve_espn_id(player_name: str) -> str | None:
    key = normalize_name(player_name)
    if not key:
        return None
    if key in OVERRIDES:
        return OVERRIDES[key]
    return _name_map(id(current_app._get_current_object())).get(key)


def clear_cache() -> None:
    _name_map.cache_clear()
