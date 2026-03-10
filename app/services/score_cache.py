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
import time as _time
from typing import Optional

logger = logging.getLogger(__name__)

_TTL = 300  # 5 minutes

_cache: dict = {
    'scores': None,       # list[dict] | None
    'ts': 0.0,            # monotonic timestamp of last successful fill
}


def get_todays_scores() -> list:
    """Return cached ``score_all_todays_props()`` result, recomputing when stale."""
    now = _time.monotonic()
    if _cache['scores'] is not None and (now - _cache['ts']) < _TTL:
        return _cache['scores']

    t0 = _time.perf_counter()
    try:
        from app.services.projection_engine import ProjectionEngine
        from app.services.value_detector import ValueDetector
        scores = ValueDetector(ProjectionEngine()).score_all_todays_props()
    except Exception as exc:
        logger.error("score_cache: scoring run failed: %s", exc)
        scores = _cache['scores'] or []   # serve stale on error rather than empty

    elapsed = _time.perf_counter() - t0
    logger.info("score_cache: filled %d scores in %.2fs", len(scores), elapsed)

    _cache['scores'] = scores
    _cache['ts'] = now
    return scores


def invalidate_scores() -> None:
    """Drop the cached scores so the next call to ``get_todays_scores()``
    triggers a fresh scoring run.  Called by the scheduler after each
    ``run_projections()`` pass."""
    _cache['scores'] = None
    _cache['ts'] = 0.0
    logger.debug("score_cache: invalidated")


def peek_age() -> Optional[float]:
    """Return seconds since the cache was last filled, or None if empty."""
    if _cache['scores'] is None:
        return None
    return _time.monotonic() - _cache['ts']
