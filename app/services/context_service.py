"""Injury and rest context service.

Fetches injury reports and schedule context to support the projection
engine's situational adjustments.
"""

import logging
import re
from datetime import datetime, timezone, date as date_type, timedelta
from typing import Optional

import requests

from app import db
from app.models import InjuryReport

logger = logging.getLogger(__name__)

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


def refresh_injuries() -> int:
    """Refresh the injury report table with latest data.

    Clears today's entries and replaces them.  Returns count of injuries stored.
    """
    today = date_type.today()
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
    """
    report = (
        InjuryReport.query
        .filter(InjuryReport.player_name.ilike(f'%{player_name}%'))
        .order_by(InjuryReport.date_reported.desc())
        .first()
    )

    if not report:
        return {}

    return {
        'status': report.status,
        'detail': report.detail or '',
        'date_reported': report.date_reported,
        'team': report.team or '',
    }


def is_player_available(player_name: str) -> bool:
    """Return True if the player is not listed as out or doubtful."""
    status = get_player_injury_status(player_name)
    if not status:
        return True
    return status.get('status', '') not in ('out', 'doubtful')


def check_back_to_back(team_name: str) -> bool:
    """Check if a team played yesterday (back-to-back situation).

    Uses ESPN scoreboard for yesterday's date.
    """
    yesterday = date_type.today() - timedelta(days=1)
    date_str = yesterday.strftime('%Y%m%d')

    try:
        resp = requests.get(
            ESPN_SCOREBOARD_URL,
            params={'dates': date_str},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        logger.error("ESPN scoreboard fetch for B2B check failed: %s", exc)
        return False

    team_lower = team_name.lower().strip()
    for event in data.get('events', []):
        comp = event.get('competitions', [{}])[0]
        for team in comp.get('competitors', []):
            name = team.get('team', {}).get('displayName', '').lower()
            if team_lower in name or name in team_lower:
                return True

    return False


def get_days_rest(team_name: str, check_days: int = 5) -> int:
    """Return the number of days since the team's last game.

    Checks the last ``check_days`` worth of ESPN scoreboards.
    Returns 0 if played yesterday, 1 if day before, etc.
    Defaults to 2 if no recent game is found.
    """
    today = date_type.today()
    team_lower = team_name.lower().strip()

    for days_ago in range(1, check_days + 1):
        check_date = today - timedelta(days=days_ago)
        date_str = check_date.strftime('%Y%m%d')

        try:
            resp = requests.get(
                ESPN_SCOREBOARD_URL,
                params={'dates': date_str},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError):
            continue

        for event in data.get('events', []):
            comp = event.get('competitions', [{}])[0]
            for team in comp.get('competitors', []):
                name = team.get('team', {}).get('displayName', '').lower()
                if team_lower in name or name in team_lower:
                    return days_ago

    return 2  # Default assumption


def get_game_context(player_name: str, team_name: str) -> dict:
    """Build a full context dict for a player's upcoming game.

    Returns {injury_status, back_to_back, days_rest, is_available}.
    """
    injury = get_player_injury_status(player_name)
    b2b = check_back_to_back(team_name)
    days_rest = 0 if b2b else get_days_rest(team_name)

    return {
        'injury_status': injury.get('status', 'healthy'),
        'injury_detail': injury.get('detail', ''),
        'back_to_back': b2b,
        'days_rest': days_rest,
        'is_available': is_player_available(player_name),
    }
