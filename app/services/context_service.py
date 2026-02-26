"""Injury and rest context service.

Fetches injury reports and schedule context to support the projection
engine's situational adjustments.
"""

import logging
import re
from datetime import datetime, timezone, date as date_type, timedelta

import requests

from app import db
from app.models import InjuryReport

logger = logging.getLogger(__name__)

ESPN_INJURIES_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries"
)
ESPN_SCOREBOARD_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
)


def fetch_espn_injuries() -> list:
    """Fetch current NBA injury data from ESPN.

    Returns a list of dicts: {player_name, team, status, detail}.
    """
    try:
        resp = requests.get(ESPN_INJURIES_URL, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        logger.error("ESPN injury fetch failed: %s", exc)
        return []

    injuries = []
    for team_block in data.get('items', data.get('teams', [])):
        team_name = ''
        team_obj = team_block.get('team', {})
        if team_obj:
            team_name = team_obj.get('displayName', team_obj.get('name', ''))

        for athlete in team_block.get('injuries', team_block.get('athletes', [])):
            player_info = athlete.get('athlete', athlete)
            player_name = player_info.get('displayName', player_info.get('fullName', ''))
            if not player_name:
                continue

            status_raw = athlete.get('status', athlete.get('type', {}).get('name', ''))
            if isinstance(status_raw, dict):
                status_raw = status_raw.get('type', status_raw.get('name', ''))
            status = _normalize_injury_status(str(status_raw))

            detail = athlete.get('details', athlete.get('longComment', ''))
            if isinstance(detail, dict):
                detail = detail.get('detail', str(detail))

            injuries.append({
                'player_name': player_name,
                'team': team_name,
                'status': status,
                'detail': str(detail)[:300] if detail else '',
            })

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
