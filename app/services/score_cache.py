"""Process-level cache for today's scored props.

All routes that display prop analysis (dashboard, /nba/analysis,
/nba/stat-analysis) share a single ``score_all_todays_props()`` result.
Without this, each page load triggers a full independent scoring run
(~1–5 s of ML inference + ESPN/Odds-API calls).

TTL: 5 minutes.  The APScheduler ``run_projections`` job calls
``invalidate_scores()`` after each scheduled scoring pass so the next
request re-warms from fresh data.
"""

import logging
from copy import deepcopy
from threading import RLock
import time as _time
from typing import Optional

from app.utils.time_helpers import et_date_str

logger = logging.getLogger(__name__)

_TTL = 300  # 5 minutes
_MAX_STALE = 600  # Never serve an old slate indefinitely during an outage.
_ERROR_RETRY_SECONDS = 30
_lock = RLock()

_cache: dict = {
    'scores': None,       # list[dict] | None
    'ts': 0.0,            # monotonic timestamp of last successful fill
    'date': None,         # ET date belonging to these scores
    'retry_at': 0.0,
}


def get_todays_scores() -> list:
    """Return cached ``score_all_todays_props()`` result, recomputing when stale."""
    with _lock:
        today = et_date_str()
        if _cache['date'] != today:
            invalidate_scores()
            _cache['date'] = today
        now = _time.monotonic()
        if _cache['scores'] is not None and (now - _cache['ts']) < _TTL:
            return _current_score_copy(_cache['scores'])
        if now < _cache['retry_at']:
            return _bounded_stale_scores(now)

        t0 = _time.perf_counter()
        try:
            from app.services.nba_service import get_todays_games
            from app.services.projection_engine import ProjectionEngine
            from app.services.value_detector import (
                CurrentQuotesUnavailable, ValueDetector, current_scored_quotes,
            )
            # Explicit games bypass the detector's separate 10-minute cache:
            # this cache owns the freshness of the result it timestamps.
            games = get_todays_games(raise_on_error=True)
            scores = ValueDetector(ProjectionEngine()).score_all_todays_props(games=games)
            current_scores = current_scored_quotes(scores)
            if scores and not current_scores:
                raise CurrentQuotesUnavailable('Scoring returned no current offered quotes')
            scores = current_scores
        except Exception as exc:
            logger.error("score_cache: scoring run failed: %s", exc)
            now = _time.monotonic()
            _cache['retry_at'] = now + _ERROR_RETRY_SECONDS
            return _bounded_stale_scores(now)

        elapsed = _time.perf_counter() - t0
        logger.info("score_cache: filled %d scores in %.2fs", len(scores), elapsed)
        # A run straddling ET midnight belongs to the old slate; do not return
        # or cache it as the new day's analysis.
        if et_date_str() != today:
            invalidate_scores()
            return []
        _cache['scores'] = deepcopy(scores)
        _cache['ts'] = _time.monotonic()
        _cache['retry_at'] = 0.0
        return deepcopy(scores)


def _current_score_copy(scores: list) -> list:
    from app.services.value_detector import current_scored_quotes
    return deepcopy(current_scored_quotes(scores))


def _bounded_stale_scores(now: float) -> list:
    if (_cache['date'] == et_date_str() and _cache['scores'] is not None
            and now - _cache['ts'] <= _MAX_STALE):
        return _current_score_copy(_cache['scores'])
    return []


def invalidate_scores() -> None:
    """Drop the cached scores so the next call to ``get_todays_scores()``
    triggers a fresh scoring run.  Called by the scheduler after each
    ``run_projections()`` pass."""
    with _lock:
        _cache.update(scores=None, ts=0.0, date=None, retry_at=0.0)
    logger.debug("score_cache: invalidated")


def peek_age() -> Optional[float]:
    """Return seconds since the cache was last filled, or None if empty."""
    with _lock:
        if _cache['scores'] is None or _cache['date'] != et_date_str():
            return None
        return _time.monotonic() - _cache['ts']
