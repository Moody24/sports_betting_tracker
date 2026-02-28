import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

import requests

from app.enums import BetType, Outcome
from app.services.base import SportService, SPORT_REGISTRY

logger = logging.getLogger(__name__)
APP_TIMEZONE = ZoneInfo("America/New_York")

ESPN_SCOREBOARD_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
)
ESPN_SUMMARY_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary"
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

# Maps prop_type value → ESPN box score column header
_PROP_STAT_COLUMN = {
    "player_points": "PTS",
    "player_rebounds": "REB",
    "player_assists": "AST",
    "player_threes": "3PT",  # "M-A" format; we take the made count
    "player_blocks": "BLK",
    "player_steals": "STL",
}


def _get_odds_api_key() -> str:
    return os.getenv("ODDS_API_KEY", "")


# ── ESPN: live scores ────────────────────────────────────────────────


def fetch_espn_scoreboard(date_str: Optional[str] = None) -> list[dict]:
    """Return NBA games from the ESPN scoreboard endpoint.

    Pass date_str as 'YYYYMMDD' to fetch a specific date; omit for today.
    """
    params = {}
    if date_str:
        params["dates"] = date_str

    try:
        resp = requests.get(ESPN_SCOREBOARD_URL, params=params, timeout=10)
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


def fetch_espn_boxscore(espn_id: str) -> dict:
    """Fetch final player stats for a completed game.

    Returns a dict keyed by player display name, each value being a dict of
    {prop_type: stat_value} e.g. {"LeBron James": {"player_points": 28, ...}}.
    """
    try:
        resp = requests.get(ESPN_SUMMARY_URL, params={"event": espn_id}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        logger.error("ESPN summary fetch failed for event %s: %s", espn_id, exc)
        return {}

    player_stats: dict = {}

    for team_block in data.get("boxscore", {}).get("players", []):
        for stat_block in team_block.get("statistics", []):
            column_names: list[str] = stat_block.get("names", [])
            for athlete in stat_block.get("athletes", []):
                name = athlete.get("athlete", {}).get("displayName", "")
                if not name:
                    continue
                raw_stats: list[str] = athlete.get("stats", [])
                entry: dict = {}
                for prop_type, col_header in _PROP_STAT_COLUMN.items():
                    if col_header not in column_names:
                        continue
                    idx = column_names.index(col_header)
                    if idx >= len(raw_stats):
                        continue
                    raw = raw_stats[idx]
                    # "3PT" comes as "M-A"; take made count
                    if "-" in str(raw):
                        try:
                            raw = raw.split("-")[0]
                        except Exception:
                            continue
                    try:
                        entry[prop_type] = float(raw)
                    except (ValueError, TypeError):
                        pass
                if entry:
                    player_stats[name] = entry

    return player_stats


# ── The Odds API: over/under lines ──────────────────────────────────


def fetch_odds() -> dict:
    """Return over/under lines from The Odds API (needs ODDS_API_KEY env var)."""
    totals, _ = fetch_odds_combined()
    return totals


def fetch_odds_combined() -> tuple:
    """Return (totals_map, h2h_map) from a single Odds API request.

    totals_map: {matchup_key -> over_under_line (float)}
    h2h_map:    {matchup_key -> {"home": int, "away": int}}
    """
    api_key = _get_odds_api_key()
    if not api_key:
        logger.warning("ODDS_API_KEY not set – odds unavailable")
        return {}, {}

    try:
        resp = requests.get(
            ODDS_API_URL,
            params={
                "apiKey": api_key,
                "regions": "us",
                "markets": "totals,h2h",
                "oddsFormat": "american",
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        logger.error("Odds API (combined) fetch failed: %s", exc)
        return {}, {}

    totals_map: dict = {}
    h2h_map: dict = {}

    for game in data:
        home_team = game.get("home_team", "")
        away_team = game.get("away_team", "")
        key = _matchup_key(home_team, away_team)

        ou_line = None
        home_ml = away_ml = None

        for bookmaker in game.get("bookmakers", []):
            for market in bookmaker.get("markets", []):
                mkey = market.get("key", "")

                if mkey == "totals" and ou_line is None:
                    for outcome in market.get("outcomes", []):
                        if outcome.get("name") == "Over" and outcome.get("point"):
                            ou_line = float(outcome["point"])
                            break

                if mkey == "h2h" and home_ml is None:
                    for outcome in market.get("outcomes", []):
                        name = outcome.get("name", "")
                        price = outcome.get("price")
                        if name == home_team:
                            home_ml = price
                        elif name == away_team:
                            away_ml = price

            if ou_line is not None and home_ml is not None:
                break

        if ou_line is not None:
            totals_map[key] = ou_line
        if home_ml is not None or away_ml is not None:
            h2h_map[key] = {"home": home_ml, "away": away_ml}

    return totals_map, h2h_map


# ── Merge scores + odds ─────────────────────────────────────────────


def _matchup_key(team_a: str, team_b: str) -> tuple:
    """Normalised key for matching ESPN names with Odds API names."""
    return tuple(sorted([_normalize_team_name(team_a), _normalize_team_name(team_b)]))


def _normalize_team_name(name: str) -> str:
    """Normalize team names across ESPN/Odds API variants."""
    if not name:
        return ""
    norm = " ".join(str(name).lower().strip().split())
    aliases = {
        "la clippers": "los angeles clippers",
        "la lakers": "los angeles lakers",
        "ny knicks": "new york knicks",
        "okc thunder": "oklahoma city thunder",
        "gs warriors": "golden state warriors",
        "no pelicans": "new orleans pelicans",
    }
    return aliases.get(norm, norm)


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


def fetch_upcoming_games() -> list[dict]:
    """Return tomorrow's NBA games from The Odds API (pre-game lines available).

    Returns a list of dicts with team names, date, event id, and O/U line.
    """
    api_key = _get_odds_api_key()
    if not api_key:
        logger.warning("ODDS_API_KEY not set – upcoming games unavailable")
        return []

    now_et = datetime.now(APP_TIMEZONE)
    tomorrow = now_et.date() + timedelta(days=1)
    day_after = tomorrow + timedelta(days=1)

    # Query the Odds API using a UTC window that corresponds to "tomorrow" in ET.
    start_et = datetime.combine(tomorrow, datetime.min.time(), tzinfo=APP_TIMEZONE)
    end_et = datetime.combine(day_after, datetime.min.time(), tzinfo=APP_TIMEZONE)
    start_utc = start_et.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    end_utc = end_et.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    try:
        resp = requests.get(
            ODDS_API_URL,
            params={
                "apiKey": api_key,
                "regions": "us",
                "markets": "totals",
                "oddsFormat": "american",
                "commenceTimeFrom": start_utc,
                "commenceTimeTo": end_utc,
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        logger.error("Odds API (upcoming games) fetch failed: %s", exc)
        return []

    games = []
    for game in data:
        home = game.get("home_team", "")
        away = game.get("away_team", "")
        commence = game.get("commence_time", "")

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

        # Parse commence_time to a date string for the form
        match_date = tomorrow.isoformat()
        if commence:
            try:
                match_date = datetime.fromisoformat(
                    commence.replace("Z", "+00:00")
                ).date().isoformat()
            except ValueError:
                pass

        games.append({
            "espn_id": game.get("id", ""),
            "home": {"name": home, "score": 0, "logo": "", "abbr": ""},
            "away": {"name": away, "score": 0, "logo": "", "abbr": ""},
            "start_time": commence,
            "match_date": match_date,
            "status": "STATUS_SCHEDULED",
            "status_detail": "Tomorrow",
            "clock": "",
            "period": 0,
            "total_score": 0,
            "over_under_line": line,
            "odds_event_id": game.get("id", ""),
        })

    return games


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
    """Combined view: ESPN live scores merged with Odds API lines + moneylines."""
    today_et = datetime.now(APP_TIMEZONE).strftime("%Y%m%d")
    games = fetch_espn_scoreboard(date_str=today_et)
    totals, h2h = fetch_odds_combined()
    events = fetch_odds_events()

    for game in games:
        key = _matchup_key(game["home"]["name"], game["away"]["name"])
        game["over_under_line"] = totals.get(key)
        game["odds_event_id"] = events.get(key, "")
        ml = h2h.get(key, {})
        game["moneyline_home"] = ml.get("home")
        game["moneyline_away"] = ml.get("away")

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
    """Check ESPN for final scores and resolve all pending bet types.

    Handles over/under, moneyline, and player prop bets.
    Returns list of (bet, new_outcome, actual_value) for bets that can be graded.
    """
    # Collect scoreboards for relevant bet dates so older pending bets can settle.
    # Fallback to "today" when no usable bet dates are present.
    scoreboards: list[dict] = []
    date_keys: set[str] = set()
    for bet in pending_bets:
        match_dt = getattr(bet, "match_date", None)
        if not match_dt:
            continue
        try:
            match_date = match_dt.date() if isinstance(match_dt, datetime) else match_dt
            for delta_days in (-1, 0, 1):
                day = match_date + timedelta(days=delta_days)
                date_keys.add(day.strftime("%Y%m%d"))
        except Exception:
            continue

    if date_keys:
        for date_key in sorted(date_keys):
            scoreboards.extend(fetch_espn_scoreboard(date_str=date_key))
    else:
        scoreboards = fetch_espn_scoreboard()

    espn_lookup: dict = {}
    for g in scoreboards:
        espn_lookup[g["espn_id"]] = g

    # Cache box scores so we only fetch each game once
    boxscore_cache: dict = {}

    results = []
    for bet in pending_bets:
        if not bet.external_game_id:
            continue
        game = espn_lookup.get(bet.external_game_id)
        if not game:
            continue
        if game["status"] != _STATUS_FINAL:
            continue

        # ── Over / Under ─────────────────────────────────────────────
        if bet.bet_type in (BetType.OVER.value, BetType.UNDER.value) and not bet.is_player_prop:
            if bet.over_under_line is None:
                continue
            actual_total = float(game["total_score"])
            if actual_total == bet.over_under_line:
                outcome = Outcome.PUSH.value
            elif bet.bet_type == BetType.OVER.value:
                outcome = Outcome.WIN.value if actual_total > bet.over_under_line else Outcome.LOSE.value
            else:
                outcome = Outcome.WIN.value if actual_total < bet.over_under_line else Outcome.LOSE.value
            results.append((bet, outcome, actual_total))

        # ── Moneyline ────────────────────────────────────────────────
        elif bet.bet_type == BetType.MONEYLINE.value:
            if not bet.picked_team:
                continue
            home = game["home"]
            away = game["away"]
            if home["score"] > away["score"]:
                winner = home["name"]
            elif away["score"] > home["score"]:
                winner = away["name"]
            else:
                # Tie (unlikely in NBA)
                results.append((bet, Outcome.PUSH.value, 0.0))
                continue
            picked_lower = bet.picked_team.lower().strip()
            winner_lower = winner.lower().strip()
            outcome = Outcome.WIN.value if picked_lower in winner_lower or winner_lower in picked_lower else Outcome.LOSE.value
            results.append((bet, outcome, float(home["score"] if home["name"] == winner else away["score"])))

        # ── Player Prop ──────────────────────────────────────────────
        elif bet.is_player_prop:
            if not bet.player_name or not bet.prop_type or bet.prop_line is None:
                continue
            espn_id = bet.external_game_id
            if espn_id not in boxscore_cache:
                boxscore_cache[espn_id] = fetch_espn_boxscore(espn_id)
            boxscore = boxscore_cache[espn_id]

            # Fuzzy match player name
            actual_stat = None
            bet_name_lower = bet.player_name.lower().strip()
            for player_name, stats in boxscore.items():
                if bet_name_lower in player_name.lower() or player_name.lower() in bet_name_lower:
                    actual_stat = stats.get(bet.prop_type)
                    break

            if actual_stat is None:
                logger.warning(
                    "Could not find stat %s for player %s in game %s",
                    bet.prop_type, bet.player_name, espn_id,
                )
                continue

            if actual_stat == bet.prop_line:
                outcome = Outcome.PUSH.value
            elif bet.bet_type == BetType.OVER.value:
                outcome = Outcome.WIN.value if actual_stat > bet.prop_line else Outcome.LOSE.value
            else:
                outcome = Outcome.WIN.value if actual_stat < bet.prop_line else Outcome.LOSE.value

            results.append((bet, outcome, actual_stat))

    return results


# ── Concrete SportService implementation ─────────────────────────────


class NBAService(SportService):
    """NBA-specific implementation backed by ESPN + The Odds API."""

    @property
    def sport_key(self) -> str:
        return "nba"

    @property
    def display_name(self) -> str:
        return "NBA"

    def fetch_scoreboard(self, date_str: Optional[str] = None) -> list[dict]:
        return fetch_espn_scoreboard(date_str)

    def fetch_boxscore(self, game_id: str) -> dict:
        return fetch_espn_boxscore(game_id)

    def fetch_odds_combined(self) -> tuple:
        return fetch_odds_combined()

    def fetch_odds_events(self) -> dict:
        return fetch_odds_events()

    def fetch_upcoming_games(self) -> list[dict]:
        return fetch_upcoming_games()

    def fetch_player_props(self, event_id: str) -> dict:
        return fetch_player_props_for_event(event_id)

    def get_todays_games(self) -> list[dict]:
        return get_todays_games()

    def get_player_props_for_game(self, game_id: str, games: Optional[list[dict]] = None) -> dict:
        return get_player_props(game_id, games)

    def resolve_pending_bets(self, pending_bets: list) -> list[tuple]:
        return resolve_pending_bets(pending_bets)

    def get_prop_markets(self) -> list[str]:
        return list(PLAYER_PROP_MARKETS)


# Register so other code can do  get_sport_service("nba")
SPORT_REGISTRY["nba"] = NBAService()
