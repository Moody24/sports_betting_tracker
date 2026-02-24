import os
from datetime import datetime, timezone

import requests

ESPN_SCOREBOARD_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
)
ODDS_API_URL = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds/"


def _get_odds_api_key():
    return os.getenv("ODDS_API_KEY", "")


# ── ESPN: live scores ────────────────────────────────────────────────


def fetch_espn_scoreboard():
    """Return today's NBA games from the free ESPN scoreboard endpoint."""
    try:
        resp = requests.get(ESPN_SCOREBOARD_URL, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError):
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


def fetch_odds():
    """Return over/under lines from The Odds API (needs ODDS_API_KEY env var)."""
    api_key = _get_odds_api_key()
    if not api_key:
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
    except (requests.RequestException, ValueError):
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


def _matchup_key(team_a, team_b):
    """Normalised key for matching ESPN names with Odds API names."""
    return tuple(sorted([team_a.lower().strip(), team_b.lower().strip()]))


def get_todays_games():
    """Combined view: ESPN live scores merged with Odds API over/under lines."""
    games = fetch_espn_scoreboard()
    odds = fetch_odds()

    for game in games:
        key = _matchup_key(game["home"]["name"], game["away"]["name"])
        game["over_under_line"] = odds.get(key)

    return games


# ── Result checker ───────────────────────────────────────────────────


def resolve_pending_bets(pending_bets):
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
        if game["status"] != "STATUS_FINAL":
            continue

        actual_total = float(game["total_score"])

        if bet.bet_type == "over":
            outcome = "win" if actual_total > bet.over_under_line else "lose"
        elif bet.bet_type == "under":
            outcome = "win" if actual_total < bet.over_under_line else "lose"
        else:
            continue

        results.append((bet, outcome, actual_total))

    return results
