"""Injury and rest context service.

Fetches injury reports and schedule context to support the projection
engine's situational adjustments.
"""

import logging
import time as _time
from datetime import datetime, date as date_type, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

import requests

from app import db
from app.models import InjuryReport

logger = logging.getLogger(__name__)
APP_TIMEZONE = ZoneInfo("America/New_York")

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

ESPN_INJURIES_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries"
)
ESPN_TEAMS_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams"
)
ESPN_SCOREBOARD_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
)


def _safe_get_json(url: str, *, params: Optional[dict] = None, timeout: int = 10, attempts: int = 2) -> dict:
    headers = {"User-Agent": "sports-betting-tracker/1.0"}
    for attempt in range(1, attempts + 1):
        try:
            resp = requests.get(url, params=params, timeout=timeout, headers=headers)
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as exc:
            if attempt == attempts:
                logger.error("Request failed for %s: %s", url, exc)
            else:
                logger.warning("Request retry %d/%d for %s", attempt, attempts, url)
    return {}


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


def _fetch_team_injuries_fallback() -> list:
    teams_payload = _safe_get_json(ESPN_TEAMS_URL, timeout=10, attempts=2)
    teams = teams_payload.get('sports', [{}])[0].get('leagues', [{}])[0].get('teams', [])
    if not teams:
        return []

    injuries = []
    seen = set()
    for team in teams:
        team_obj = team.get('team', {})
        team_id = team_obj.get('id')
        if not team_id:
            continue
        team_inj_url = f"{ESPN_TEAMS_URL}/{team_id}/injuries"
        payload = _safe_get_json(team_inj_url, timeout=8, attempts=2)
        parsed = _parse_injury_payload(payload)
        for item in parsed:
            key = (item['player_name'].lower().strip(), item['status'], item.get('team', '').lower().strip())
            if key in seen:
                continue
            seen.add(key)
            injuries.append(item)
    return injuries


def fetch_espn_injuries() -> list:
    """Fetch current NBA injury data from ESPN.

    Returns a list of dicts: {player_name, team, status, detail}.
    """
    data = _safe_get_json(ESPN_INJURIES_URL, timeout=10, attempts=2)
    injuries = _parse_injury_payload(data) if data else []
    if injuries:
        return injuries

    # Fallback path: fetch per-team injury feeds if the aggregate endpoint is empty.
    fallback = _fetch_team_injuries_fallback()
    if fallback:
        logger.info("Injury fallback endpoint returned %d rows", len(fallback))
        return fallback

    return injuries


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


def _today_et() -> date_type:
    return datetime.now(APP_TIMEZONE).date()


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
        resp = requests.get(
            ESPN_SCOREBOARD_URL,
            params={'dates': date_str},
            timeout=10,
            headers={"User-Agent": "sports-betting-tracker/1.0"},
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("ESPN scoreboard fetch failed for %s: %s", date_str, exc)
        data = {}

    elapsed = _time.perf_counter() - t0
    logger.debug("PERF scoreboard fetch date=%s elapsed=%.2fs events=%d",
                 date_str, elapsed, len(data.get('events', [])))

    today_str = _today_et().strftime('%Y%m%d')
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
    today = _today_et()
    injuries = fetch_espn_injuries()
    if not injuries:
        cloned = _clone_latest_injuries_for_today(today)
        if cloned:
            logger.info("No fresh injuries fetched; copied %d latest rows forward", cloned)
            return cloned
        logger.info("No injuries fetched (or empty list)")
        return 0

    # Delete today's existing entries and replace
    InjuryReport.query.filter_by(date_reported=today).delete()

    count = 0
    for inj in injuries:
        report = InjuryReport(
            player_name=inj['player_name'],
            team=inj.get('team', ''),
            status=inj['status'],
            detail=inj.get('detail', ''),
            date_reported=today,
        )
        db.session.add(report)
        count += 1

    db.session.commit()
    logger.info("Refreshed %d injury reports", count)
    return count


def _clone_latest_injuries_for_today(today: date_type) -> int:
    """Carry forward the latest available injury snapshot when upstream is empty."""
    latest_date = db.session.query(db.func.max(InjuryReport.date_reported)).scalar()
    if not latest_date or latest_date == today:
        return 0

    latest_rows = InjuryReport.query.filter_by(date_reported=latest_date).all()
    if not latest_rows:
        return 0

    InjuryReport.query.filter_by(date_reported=today).delete()
    for row in latest_rows:
        db.session.add(InjuryReport(
            player_name=row.player_name,
            team=row.team,
            status=row.status,
            detail=row.detail,
            date_reported=today,
        ))
    db.session.commit()
    return len(latest_rows)


def get_player_injury_status(player_name: str) -> dict:
    """Look up the most recent injury status for a player.

    Returns a dict with {status, detail, date_reported} or empty dict.

    Results are cached process-wide for 5 min so the scoring loop does not
    fire an ilike DB query for every player-prop combination.
    """
    name_lower = player_name.lower().strip()
    now = _time.monotonic()
    cached = _INJURY_CACHE.get(name_lower)
    if cached is not None:
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
    yesterday = _today_et() - timedelta(days=1)
    date_str = yesterday.strftime('%Y%m%d')
    return _team_played_on_date(team_name, date_str)


def get_days_rest(team_name: str, check_days: int = 5) -> int:
    """Return the number of days since the team's last game.

    Checks the last ``check_days`` worth of ESPN scoreboards.
    Each date's scoreboard is fetched at most once (shared cache).
    Defaults to 2 if no recent game is found.
    """
    today = _today_et()
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
    today_str = _today_et().strftime('%Y%m%d')
    ctx_key = (team_name.lower().strip(), today_str)

    now = _time.monotonic()
    cached = _GAME_CONTEXT_CACHE.get(ctx_key)
    # The context dict carries an '_expires' sentinel for TTL management.
    if cached and now < cached.get('_expires', 0):
        # Injury status is player-specific — update it from DB without re-fetching ESPN.
        injury = get_player_injury_status(player_name)
        result = dict(cached)
        result['injury_status'] = injury.get('status', 'healthy')
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
        'injury_status': injury.get('status', 'healthy'),
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
