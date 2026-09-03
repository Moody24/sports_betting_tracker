"""HTTP adapter for ESPN's public NBA endpoints.

This module owns request construction, retries, timeouts, and JSON decoding.
Callers remain responsible for translating provider payloads into domain data.
"""

import logging

import requests

from app.config_display import PROP_ESPN_COLUMN

logger = logging.getLogger(__name__)

ESPN_BASE_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
)
ESPN_SCOREBOARD_URL = f"{ESPN_BASE_URL}/scoreboard"
ESPN_SUMMARY_URL = f"{ESPN_BASE_URL}/summary"
ESPN_INJURIES_URL = f"{ESPN_BASE_URL}/injuries"
ESPN_TEAMS_URL = f"{ESPN_BASE_URL}/teams"

_HEADERS = {"User-Agent": "sports-betting-tracker/1.0"}


class EspnClientError(RuntimeError):
    """Raised when an ESPN request cannot return a valid JSON payload."""


def extract_prop_boxscore(summary_payload: dict) -> dict:
    """Normalize ESPN player rows into prop-type values keyed by player name."""
    player_stats: dict = {}
    for team_block in summary_payload.get('boxscore', {}).get('players', []):
        for stat_block in team_block.get('statistics', []):
            column_names = stat_block.get('names', [])
            column_indexes = {name: index for index, name in enumerate(column_names)}
            for athlete in stat_block.get('athletes', []):
                name = athlete.get('athlete', {}).get('displayName', '')
                if not name:
                    continue
                raw_stats = athlete.get('stats', [])
                entry = {}
                for prop_type, column_name in PROP_ESPN_COLUMN.items():
                    index = column_indexes.get(column_name)
                    if index is None or index >= len(raw_stats):
                        continue
                    raw_value = str(raw_stats[index]).split('-', 1)[0]
                    try:
                        entry[prop_type] = float(raw_value)
                    except (TypeError, ValueError):
                        continue
                if entry:
                    points = entry.get('player_points')
                    rebounds = entry.get('player_rebounds')
                    assists = entry.get('player_assists')
                    if points is not None and rebounds is not None and assists is not None:
                        entry['player_points_rebounds_assists'] = (
                            points + rebounds + assists
                        )
                    player_stats[name] = entry
    return player_stats


def fetch_json(
    url: str,
    *,
    params: dict | None = None,
    timeout: int = 10,
    attempts: int = 1,
) -> dict:
    """Fetch an ESPN JSON object, retrying transient request failures."""
    last_error: Exception | None = None
    for attempt in range(1, max(attempts, 1) + 1):
        try:
            response = requests.get(
                url,
                params=params,
                timeout=timeout,
                headers=_HEADERS,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("ESPN response was not a JSON object")
            return payload
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            if attempt < max(attempts, 1):
                logger.warning(
                    "ESPN request retry %d/%d for %s",
                    attempt,
                    attempts,
                    url,
                )
    raise EspnClientError(f"ESPN request failed for {url}: {last_error}") from last_error


def fetch_scoreboard_payload(
    date_str: str | None = None,
    *,
    timeout: int = 10,
    attempts: int = 1,
) -> dict:
    params = {'dates': date_str} if date_str else {}
    return fetch_json(
        ESPN_SCOREBOARD_URL,
        params=params,
        timeout=timeout,
        attempts=attempts,
    )


def fetch_summary_payload(
    espn_id: str,
    *,
    timeout: int = 10,
    attempts: int = 1,
) -> dict:
    return fetch_json(
        ESPN_SUMMARY_URL,
        params={'event': espn_id},
        timeout=timeout,
        attempts=attempts,
    )


def fetch_injuries_payload(*, timeout: int = 10, attempts: int = 2) -> dict:
    return fetch_json(ESPN_INJURIES_URL, timeout=timeout, attempts=attempts)


def fetch_teams_payload(*, timeout: int = 10, attempts: int = 2) -> dict:
    return fetch_json(ESPN_TEAMS_URL, timeout=timeout, attempts=attempts)


def fetch_team_injuries_payload(
    team_id: str,
    *,
    timeout: int = 8,
    attempts: int = 2,
) -> dict:
    return fetch_json(
        f"{ESPN_TEAMS_URL}/{team_id}/injuries",
        timeout=timeout,
        attempts=attempts,
    )
