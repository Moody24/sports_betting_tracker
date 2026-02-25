import logging
import os
from datetime import datetime, timezone
from typing import Optional

import requests

from app.enums import BetType, Outcome

logger = logging.getLogger(__name__)

ESPN_SCOREBOARD_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
)
ODDS_API_URL = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds/"
ODDS_API_EVENTS_URL = "https://api.the-odds-api.com/v4/sports/basketball_nba/events/"

_STATUS_FINAL = "STATUS_FINAL"

PLAYER_PROP_MARKETS = [
    "player_points",
    "player_rebounds",
    "player_assists",
    "player_threes",
]


def _get_odds_api_key() -> str:
    return os.getenv("ODDS_API_KEY", "")


# ── ESPN: live scores ────────────────────────────────────────────────


def fetch_espn_scoreboard() -> list[dict]:
    """Return today's NBA games from the free ESPN scoreboard endpoint."""
    try:
        resp = requests.get(ESPN_SCOREBOARD_URL, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        logger.error("ESPN scoreboard fetch failed: %s", exc)
        return []

    games = []
    for event in data.get("events", []):
        comp = event.get("competitions", [{}])[0]
        competitors = comp.get("competitors", [])

        home = away = None
        for team in competitors:
            info = {
                "name": team.get("team", {}).get("displayName", ""),
                "abbr": team.get("team", {}).get("abbreviation", ""),
                "score": int(team.get("score", 0) or 0),
                "logo": team.get("team", {}).get("logo", ""),
            }
            if team.get("homeAway") == "home":
                home = info
            else:
                away = info

        if not home or not away:
            continue

        status_obj = event.get("status", {})
        status_type = status_obj.get("type", {})

        games.append(
            {
                "espn_id": event.get("id", ""),
                "name": event.get("name", ""),
                "home": home,
                "away": away,
                "total_score": home["score"] + away["score"],
                "status": status_type.get("name", ""),
                "status_detail": status_type.get("detail", "")
                or status_type.get("description", ""),
                "clock": status_obj.get("displayClock", ""),
                "period": status_obj.get("period", 0),
                "start_time": event.get("date", ""),
            }
        )

    return games


# ── The Odds API: over/under lines ──────────────────────────────────


def fetch_odds() -> dict:
    """Return over/under lines from The Odds API (needs ODDS_API_KEY env var)."""
    api_key = _get_odds_api_key()
    if not api_key:
        logger.warning("ODDS_API_KEY not set – over/under lines unavailable")
        return {}

    try:
        resp = requests.get(
            ODDS_API_URL,
            params={
                "apiKey": api_key,
                "regions": "us",
                "markets": "totals",
                "oddsFormat": "american",
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        logger.error("Odds API (totals) fetch failed: %s", exc)
        return {}

    odds_map = {}
    for game in data:
        home = game.get("home_team", "")
        away = game.get("away_team", "")
        key = _matchup_key(home, away)

        line = None
        for bookmaker in game.get("bookmakers", []):
            for market in bookmaker.get("markets", []):
                if market.get("key") == "totals":
                    for outcome in market.get("outcomes", []):
                        if outcome.get("name") == "Over" and outcome.get("point"):
                            line = float(outcome["point"])
                            break
                if line is not None:
                    break
            if line is not None:
                break

        if line is not None:
            odds_map[key] = line

    return odds_map


# ── Merge scores + odds ─────────────────────────────────────────────


def _matchup_key(team_a: str, team_b: str) -> tuple[str, str]:
    """Normalised key for matching ESPN names with Odds API names."""
    return tuple(sorted([team_a.lower().strip(), team_b.lower().strip()]))


def fetch_odds_events() -> dict:
    """Return Odds API events with their IDs, mapped by matchup key."""
    api_key = _get_odds_api_key()
    if not api_key:
        logger.warning("ODDS_API_KEY not set – player props unavailable")
        return {}

    try:
        resp = requests.get(
            ODDS_API_EVENTS_URL,
            params={"apiKey": api_key},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        logger.error("Odds API (events) fetch failed: %s", exc)
        return {}

    event_map = {}
    for event in data:
        home = event.get("home_team", "")
        away = event.get("away_team", "")
        key = _matchup_key(home, away)
        event_map[key] = event.get("id", "")

    return event_map


def fetch_player_props_for_event(odds_event_id: str) -> dict:
    """Fetch player prop lines for a specific Odds API event.

    Returns a dict keyed by market name, each containing a list of
    {player, line, over_odds, under_odds} dicts.
    """
    api_key = _get_odds_api_key()
    if not api_key or not odds_event_id:
        return {}

    url = f"{ODDS_API_EVENTS_URL}{odds_event_id}/odds"
    try:
        resp = requests.get(
            url,
            params={
                "apiKey": api_key,
                "regions": "us",
                "markets": ",".join(PLAYER_PROP_MARKETS),
                "oddsFormat": "american",
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        logger.error("Odds API (player props) fetch failed for event %s: %s", odds_event_id, exc)
        return {}

    props = {}
    seen = {}  # track best line per (market, player) to dedupe bookmakers

    for bookmaker in data.get("bookmakers", []):
        for market in bookmaker.get("markets", []):
            market_key = market.get("key", "")
            if market_key not in PLAYER_PROP_MARKETS:
                continue

            outcomes = market.get("outcomes", [])
            # Group outcomes by player
            player_lines = {}
            for outcome in outcomes:
                player = outcome.get("description", "")
                if not player:
                    continue
                if player not in player_lines:
                    player_lines[player] = {}
                side = outcome.get("name", "").lower()
                player_lines[player][side] = {
                    "odds": outcome.get("price", 0),
                    "point": outcome.get("point"),
                }

            for player, sides in player_lines.items():
                dedup_key = (market_key, player)
                if dedup_key in seen:
                    continue
                seen[dedup_key] = True

                over = sides.get("over", {})
                under = sides.get("under", {})
                line = over.get("point") or under.get("point")
                if line is None:
                    continue

                props.setdefault(market_key, []).append({
                    "player": player,
                    "line": float(line),
                    "over_odds": over.get("odds", 0),
                    "under_odds": under.get("odds", 0),
                })

    # Sort each market by player name
    for market_key in props:
        props[market_key].sort(key=lambda p: p["player"])

    return props


def get_todays_games() -> list[dict]:
    """Combined view: ESPN live scores merged with Odds API over/under lines."""
    games = fetch_espn_scoreboard()
    odds = fetch_odds()
    events = fetch_odds_events()

    for game in games:
        key = _matchup_key(game["home"]["name"], game["away"]["name"])
        game["over_under_line"] = odds.get(key)
        game["odds_event_id"] = events.get(key, "")

    return games


def get_player_props(espn_id: str, games: Optional[list[dict]] = None) -> dict:
    """Get player props for a game identified by ESPN ID.

    Looks up the Odds API event ID via team-name matching, then fetches props.
    """
    if games is None:
        games = get_todays_games()

    for game in games:
        if game["espn_id"] == espn_id:
            return fetch_player_props_for_event(game.get("odds_event_id", ""))

    return {}


# ── Result checker ───────────────────────────────────────────────────


def resolve_pending_bets(pending_bets: list) -> list[tuple]:
    """Check ESPN for final scores and resolve over/under bets.

    Returns list of (bet, new_outcome, actual_total) for bets that can be graded.
    """
    games = fetch_espn_scoreboard()

    espn_lookup = {}
    for g in games:
        espn_lookup[g["espn_id"]] = g

    results = []
    for bet in pending_bets:
        if not bet.external_game_id:
            continue
        game = espn_lookup.get(bet.external_game_id)
        if not game:
            continue
        if game["status"] != _STATUS_FINAL:
            continue

        actual_total = float(game["total_score"])

        if actual_total == bet.over_under_line:
            outcome = Outcome.PUSH.value
        elif bet.bet_type == BetType.OVER.value:
            outcome = Outcome.WIN.value if actual_total > bet.over_under_line else Outcome.LOSE.value
        elif bet.bet_type == BetType.UNDER.value:
            outcome = Outcome.WIN.value if actual_total < bet.over_under_line else Outcome.LOSE.value
        else:
            continue

        results.append((bet, outcome, actual_total))

    return results
