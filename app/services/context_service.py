"""Injury and rest context service.

Fetches injury reports and schedule context to support the projection
engine's situational adjustments.
"""

import logging
import time as _time
from datetime import timedelta
from flask import current_app
from app import db
from app.models import InjuryReport
from app.services.data_receipts import latest_fetch, record_fetch
from app.services.espn_client import (
    EspnClientError,
    fetch_injuries_payload,
    fetch_scoreboard_payload,
    fetch_team_injuries_payload,
    fetch_teams_payload,
)
from app.utils.time_helpers import et_today, ET

logger = logging.getLogger(__name__)
APP_TIMEZONE = ET

# ── Module-level scoreboard cache (process-scoped) ────────────────────────
# Past-date scoreboards never change, so we cache them indefinitely.
# Today's date uses a 10-min TTL to pick up late score corrections.
_SCOREBOARD_CACHE: dict[str, dict] = {}       # date_str → raw ESPN JSON
_SCOREBOARD_CACHE_TTL_PAST = 86_400           # past dates: 24 h
_SCOREBOARD_CACHE_TTL_TODAY = 600             # today's date: 10 min
_SCOREBOARD_CACHE_EXPIRES: dict[str, float] = {}

# Process-scoped game-context cache (team_name_lower, today_date_str) → ctx dict
# TTL matches the scoring cache so context doesn't go stale between scoring runs.
_GAME_CONTEXT_CACHE: dict[tuple, dict] = {}
_GAME_CONTEXT_CACHE_TTL = 600   # 10 min

# Process-scoped injury status cache: player_name_lower → (result_dict, expires_at)
# get_player_injury_status() fires an ilike DB query per call; in the scoring loop
# that's one query per player-prop.  A 5-min TTL matches the game-context TTL.
_INJURY_CACHE: dict[str, tuple] = {}   # name_lower → (result_dict, expires_monotonic)
_INJURY_CACHE_TTL = 300   # 5 min

def _parse_injury_payload(data: dict) -> list:
    injuries = []
    team_blocks = data.get('items', data.get('teams', []))
    if not team_blocks and data.get('injuries'):
        team_blocks = data.get('injuries', [])

    for team_block in team_blocks:
        team_name = ''
        team_obj = team_block.get('team', {})
        if team_obj:
            team_name = team_obj.get('displayName', team_obj.get('name', ''))
        if not team_name:
            team_name = team_block.get('displayName', '')

        for athlete in team_block.get('injuries', team_block.get('athletes', [])):
            player_info = athlete.get('athlete', athlete)
            player_name = player_info.get('displayName', player_info.get('fullName', ''))
            if not player_name:
                continue

            status_raw = athlete.get('status', athlete.get('type', {}).get('name', ''))
            if isinstance(status_raw, dict):
                status_raw = status_raw.get('type', status_raw.get('name', ''))
            status = _normalize_injury_status(str(status_raw))

            detail = athlete.get('details', athlete.get('longComment', athlete.get('shortComment', '')))
            if isinstance(detail, dict):
                detail = detail.get('detail', str(detail))

            injuries.append({
                'player_name': player_name,
                'team': team_name,
                'status': status,
                'detail': str(detail)[:300] if detail else '',
            })
    return injuries


def _fetch_team_injuries_fallback() -> tuple[list, bool]:
    try:
        teams_payload = fetch_teams_payload(timeout=10, attempts=2)
    except EspnClientError:
        teams_payload = {}
    teams = teams_payload.get('sports', [{}])[0].get('leagues', [{}])[0].get('teams', [])
    if not teams:
        return [], False

    injuries = []
    seen = set()
    complete = True
    for team in teams:
        team_obj = team.get('team', {})
        team_id = team_obj.get('id')
        if not team_id:
            continue
        try:
            payload = fetch_team_injuries_payload(
                str(team_id), timeout=8, attempts=2)
        except EspnClientError:
            payload = {}
            complete = False
        if not isinstance(payload, dict) or not any(
            key in payload for key in ('injuries', 'items', 'teams')
        ):
            complete = False
        parsed = _parse_injury_payload(payload)
        for item in parsed:
            key = (item['player_name'].lower().strip(), item['status'], item.get('team', '').lower().strip())
            if key in seen:
                continue
            seen.add(key)
            injuries.append(item)
    return injuries, complete


def _fetch_injury_report() -> tuple[list, bool]:
    try:
        data = fetch_injuries_payload(timeout=10, attempts=2)
    except EspnClientError:
        data = {}
    aggregate_complete = isinstance(data, dict) and any(
        key in data for key in ('items', 'teams', 'injuries')
    )
    injuries = _parse_injury_payload(data) if data else []
    if injuries:
        return injuries, aggregate_complete

    fallback, fallback_complete = _fetch_team_injuries_fallback()
    if fallback:
        logger.info("Injury fallback endpoint returned %d rows", len(fallback))
        return fallback, fallback_complete
    return [], aggregate_complete or fallback_complete


def fetch_espn_injuries() -> list:
    """Fetch current NBA injury data from ESPN.

    Returns a list of dicts: {player_name, team, status, detail}.
    """
    return _fetch_injury_report()[0]


def _normalize_injury_status(raw: str) -> str:
    """Normalize injury status to one of: out, doubtful, questionable, probable, day-to-day."""
    raw_lower = raw.lower().strip()
    if 'out' in raw_lower:
        return 'out'
    if 'doubtful' in raw_lower:
        return 'doubtful'
    if 'questionable' in raw_lower:
        return 'questionable'
    if 'probable' in raw_lower:
        return 'probable'
    if 'day' in raw_lower:
        return 'day-to-day'
    return raw_lower or 'unknown'



def _fetch_scoreboard_for_date(date_str: str) -> dict:
    """Fetch ESPN scoreboard for *date_str* (YYYYMMDD), with process-level caching.

    Past dates are cached for 24 h (they never change).
    Today's date is cached for 10 min to pick up late corrections.
    All callers share the same cached payload so a single date is never
    fetched more than once per TTL window, regardless of how many teams
    or players trigger a lookup.
    """
    now = _time.monotonic()
    if date_str in _SCOREBOARD_CACHE and now < _SCOREBOARD_CACHE_EXPIRES.get(date_str, 0):
        return _SCOREBOARD_CACHE[date_str]

    t0 = _time.perf_counter()
    try:
        data = fetch_scoreboard_payload(date_str)
    except EspnClientError as exc:
        logger.warning("ESPN scoreboard fetch failed for %s: %s", date_str, exc)
        data = {}

    elapsed = _time.perf_counter() - t0
    logger.debug("PERF scoreboard fetch date=%s elapsed=%.2fs events=%d",
                 date_str, elapsed, len(data.get('events', [])))

    today_str = et_today().strftime('%Y%m%d')
    ttl = _SCOREBOARD_CACHE_TTL_TODAY if date_str == today_str else _SCOREBOARD_CACHE_TTL_PAST
    _SCOREBOARD_CACHE[date_str] = data
    _SCOREBOARD_CACHE_EXPIRES[date_str] = now + ttl
    return data


def _team_played_on_date(team_name: str, date_str: str) -> bool:
    """Return True if *team_name* appears in the ESPN scoreboard for *date_str*."""
    data = _fetch_scoreboard_for_date(date_str)
    team_lower = team_name.lower().strip()
    for event in data.get('events', []):
        comp = event.get('competitions', [{}])[0]
        for team in comp.get('competitors', []):
            name = team.get('team', {}).get('displayName', '').lower()
            if team_lower in name or name in team_lower:
                return True
    return False


def refresh_injuries() -> int:
    """Refresh the injury report table with latest data.

    Clears today's entries and replaces them.  Returns count of injuries stored.
    """
    today = et_today()
    injuries, complete = _fetch_injury_report()
    if not complete:
        record_fetch('espn_injuries', 'failed', detail='No complete injury response')
        db.session.commit()
        logger.warning("Injury refresh failed; existing reports retain their original dates")
        return 0

    if not injuries and InjuryReport.query.filter_by(date_reported=today).first():
        record_fetch('espn_injuries', 'conflict', data_through=today,
                     source_version='espn_site',
                     detail='Empty direct report conflicts with dated injury rows')
        db.session.commit()
        logger.warning('Empty ESPN injury report conflicts with existing reports for today')
        return 0

    InjuryReport.query.filter_by(date_reported=today).delete()

    count = 0
    for inj in injuries:
        report = InjuryReport(
            player_name=inj['player_name'],
            team=inj.get('team', ''),
            status=inj['status'],
            detail=inj.get('detail', ''),
            date_reported=today,
            source='espn',
        )
        db.session.add(report)
        count += 1

    record_fetch('espn_injuries', 'success', count, data_through=today,
                 source_version='espn_site')
    db.session.commit()
    _INJURY_CACHE.clear()
    logger.info("Refreshed %d injury reports", count)
    return count


def get_player_injury_status(player_name: str) -> dict:
    """Look up the most recent injury status for a player.

    Returns a dict with {status, detail, date_reported} or empty dict.

    Results are cached process-wide for 5 min so the scoring loop does not
    fire an ilike DB query for every player-prop combination.
    """
    name_lower = player_name.lower().strip()
    now = _time.monotonic()
    cached = _INJURY_CACHE.get(name_lower)
    if cached is not None and not current_app.config.get('TESTING', False):
        result, expires = cached
        if now < expires:
            return result

    report = (
        InjuryReport.query
        .filter(InjuryReport.player_name.ilike(f'%{player_name}%'))
        .order_by(InjuryReport.date_reported.desc())
        .first()
    )

    if not report:
        result = {}
    else:
        result = {
            'status': report.status,
            'detail': report.detail or '',
            'date_reported': report.date_reported,
            'team': report.team or '',
        }

    _INJURY_CACHE[name_lower] = (result, now + _INJURY_CACHE_TTL)
    return result


def player_availability(player_name: str) -> str:
    """Return available, unavailable, or unknown for automatic decisions."""
    receipt = latest_fetch('espn_injuries')
    if (receipt is None or receipt.status != 'success'
            or receipt.data_through != et_today()):
        return 'unknown'
    report = (InjuryReport.query
              .filter(InjuryReport.date_reported == et_today())
              .filter(db.func.lower(InjuryReport.player_name) == player_name.strip().lower())
              .order_by(InjuryReport.fetched_at.desc(), InjuryReport.id.desc())
              .first())
    status = report.status if report else ''
    if status in ('out', 'doubtful'):
        return 'unavailable'
    if report and status not in ('healthy', 'active', 'available'):
        return 'unknown'
    return 'available'


def _current_injury_status(player_name: str, injury: dict) -> str:
    if injury.get('date_reported') == et_today():
        return injury['status']
    return 'healthy' if player_availability(player_name) == 'available' else 'unknown'


def is_player_available(player_name: str) -> bool:
    """Return True if the player is not listed as out or doubtful."""
    status = get_player_injury_status(player_name)
    if not status:
        return True
    return status.get('status', '') not in ('out', 'doubtful')


def check_back_to_back(team_name: str) -> bool:
    """Check if a team played yesterday (back-to-back situation).

    Reads from the shared per-date scoreboard cache — never makes a
    redundant ESPN request if the same date was already fetched.
    """
    yesterday = et_today() - timedelta(days=1)
    date_str = yesterday.strftime('%Y%m%d')
    return _team_played_on_date(team_name, date_str)


def get_days_rest(team_name: str, check_days: int = 5) -> int:
    """Return the number of days since the team's last game.

    Checks the last ``check_days`` worth of ESPN scoreboards.
    Each date's scoreboard is fetched at most once (shared cache).
    Defaults to 2 if no recent game is found.
    """
    today = et_today()
    for days_ago in range(1, check_days + 1):
        check_date = today - timedelta(days=days_ago)
        date_str = check_date.strftime('%Y%m%d')
        if _team_played_on_date(team_name, date_str):
            return days_ago
    return 2  # Default assumption


def get_game_context(player_name: str, team_name: str) -> dict:
    """Build a full context dict for a player's upcoming game.

    Returns {injury_status, back_to_back, days_rest, is_available}.

    Results are cached at the process level (keyed by team + today's date)
    so repeated calls across multiple scoring passes reuse the same result
    without re-fetching ESPN data.
    """
    today_str = et_today().strftime('%Y%m%d')
    ctx_key = (team_name.lower().strip(), today_str)

    now = _time.monotonic()
    cached = _GAME_CONTEXT_CACHE.get(ctx_key)
    # The context dict carries an '_expires' sentinel for TTL management.
    if cached and now < cached.get('_expires', 0):
        # Injury status is player-specific — update it from DB without re-fetching ESPN.
        injury = get_player_injury_status(player_name)
        result = dict(cached)
        result['injury_status'] = _current_injury_status(player_name, injury)
        result['injury_detail'] = injury.get('detail', '')
        result['is_available'] = is_player_available(player_name)
        result.pop('_expires', None)
        return result

    t0 = _time.perf_counter()
    b2b = check_back_to_back(team_name)
    days_rest = 0 if b2b else get_days_rest(team_name)
    elapsed = _time.perf_counter() - t0
    logger.debug("PERF get_game_context team=%s b2b=%s days_rest=%d elapsed=%.2fs",
                 team_name, b2b, days_rest, elapsed)

    # Store the team-level schedule context (no player-specific fields).
    team_ctx = {
        'back_to_back': b2b,
        'days_rest': days_rest,
        '_expires': now + _GAME_CONTEXT_CACHE_TTL,
    }
    _GAME_CONTEXT_CACHE[ctx_key] = team_ctx

    injury = get_player_injury_status(player_name)
    return {
        'injury_status': _current_injury_status(player_name, injury),
        'injury_detail': injury.get('detail', ''),
        'back_to_back': b2b,
        'days_rest': days_rest,
        'is_available': is_player_available(player_name),
    }


def clear_schedule_caches() -> None:
    """Clear all process-level schedule/context caches.

    Call at the start of a new game day so stale data is not carried forward.
    """
    _SCOREBOARD_CACHE.clear()
    _SCOREBOARD_CACHE_EXPIRES.clear()
    _GAME_CONTEXT_CACHE.clear()
    _INJURY_CACHE.clear()
    logger.info("Schedule caches cleared")
